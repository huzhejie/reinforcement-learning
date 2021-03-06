import utils
from metrics import Mean
import numpy as np
import gym
import os
from tensorboardX import SummaryWriter
import torch
import itertools
from tqdm import tqdm
from torch_rl.network import PolicyCategorical
from torch_rl.utils import batch_return


# TODO: train/eval
# TODO: bn update
# TODO: return normalization
# TODO: monitored session
# TODO: normalize advantage?


def build_batch(history):
    columns = zip(*history)

    return [torch.tensor(col, dtype=torch.float32).transpose(0, 1) for col in columns]


def build_parser():
    parser = utils.ArgumentParser()
    parser.add_argument('--learning-rate', type=float, default=1e-3)
    parser.add_argument('--experiment-path', type=str, default='./tf_log/torch/pg-mc')
    parser.add_argument('--env', type=str, required=True)
    parser.add_argument('--episodes', type=int, default=10000)
    parser.add_argument('--entropy-weight', type=float, default=1e-2)
    parser.add_argument('--gamma', type=float, default=0.99)
    parser.add_argument('--monitor', action='store_true')

    return parser


def main():
    def train_step(states, actions, rewards):
        optimizer.zero_grad()

        # actor
        dist = policy(states)
        returns = batch_return(rewards, gamma=args.gamma)
        # advantages = tf.stop_gradient(utils.normalization(returns))
        advantages = returns.detach()
        actor_loss = -torch.mean(dist.log_prob(actions) * advantages)
        actor_loss -= args.entropy_weight * torch.mean(dist.entropy())

        # training
        loss = actor_loss

        loss.backward()
        optimizer.step()
        global_step.data += 1

        return global_step, loss.item()

    args = build_parser().parse_args()
    utils.fix_seed(args.seed)
    experiment_path = os.path.join(args.experiment_path, args.env)
    env = gym.make(args.env)
    env.seed(args.seed)
    writer = SummaryWriter(experiment_path)

    if args.monitor:
        env = gym.wrappers.Monitor(env, os.path.join('./data', args.env), force=True)

    global_step = torch.tensor(0)
    policy = PolicyCategorical(np.squeeze(env.observation_space.shape), np.squeeze(env.action_space.shape))
    params = policy.parameters()
    optimizer = torch.optim.Adam(params, args.learning_rate, weight_decay=1e-4)
    metrics = {'loss': Mean(), 'ep_length': Mean(), 'ep_reward': Mean()}

    if os.path.exists(os.path.join(experiment_path, 'parameters')):
        policy.load_state_dict(torch.load(os.path.join(experiment_path, 'parameters')))

    policy.train()
    for _ in tqdm(range(args.episodes), desc='training'):
        history = []
        s = env.reset()
        ep_reward = 0

        for t in itertools.count():
            a = policy(torch.tensor(s, dtype=torch.float32)).sample().item()
            s_prime, r, d, _ = env.step(a)
            ep_reward += r

            history.append(([s], [a], [r]))

            if d:
                break
            else:
                s = s_prime

        batch = {}
        batch['states'], batch['actions'], batch['rewards'] = build_batch(history)

        step, loss = train_step(**batch)
        metrics['loss'].update(loss)
        metrics['ep_length'].update(t)
        metrics['ep_reward'].update(ep_reward)

        if step % 100 == 0:
            for k in metrics:
                writer.add_scalar(k, metrics[k].compute(), step)
            torch.save(policy.state_dict(), os.path.join(experiment_path, 'parameters'))
            {metrics[k].reset() for k in metrics}


if __name__ == '__main__':
    main()

import utils
import numpy as np
import gym
import os
import tensorflow as tf
import itertools
from tqdm import tqdm
from network import PolicyCategorical


# TODO: do not mask not taken actions?
# TODO: compute advantage out of graph


def build_parser():
    parser = utils.ArgumentParser()
    parser.add_argument('--history-size', type=int, default=10000)
    parser.add_argument('--learning-rate', type=float, default=1e-3)
    parser.add_argument('--experiment-path', type=str, default='./tf_log/ppo-pg-mc')
    parser.add_argument('--env', type=str, required=True)
    parser.add_argument('--episodes', type=int, default=1000)
    parser.add_argument('--gamma', type=float, default=0.99)
    parser.add_argument('--monitor', action='store_true')

    return parser


def main():
    args = build_parser().parse_args()
    utils.fix_seed(args.seed)
    experiment_path = os.path.join(args.experiment_path, args.env)
    env = gym.make(args.env)
    state_size = np.squeeze(env.observation_space.shape)
    assert state_size.shape == ()

    if args.monitor:
        env = gym.wrappers.Monitor(env, os.path.join('./data', args.env), force=True)

    global_step = tf.train.get_or_create_global_step()
    training = tf.placeholder(tf.bool, [])

    # input
    state = tf.placeholder(tf.float32, [None, state_size], name='state')
    action = tf.placeholder(tf.int32, [None], name='action')
    ret = tf.placeholder(tf.float32, [None], name='return')

    # actor
    policy = PolicyCategorical(env.action_space.n, name='policy')
    dist = policy(state, training=training)
    policy_old = PolicyCategorical(env.action_space.n, trainable=False, name='policy_old')
    dist_old = policy_old(state, training=False)
    action_sample = dist.sample()
    advantage = ret

    ratio = tf.exp(dist.log_prob(action) - dist_old.log_prob(action))  # pnew / pold
    surr1 = ratio * advantage  # surrogate from conservative policy iteration
    surr2 = tf.clip_by_value(ratio, 1.0 - 0.2, 1.0 + 0.2) * advantage  #
    actor_loss = -tf.reduce_mean(tf.minimum(surr1, surr2))  # PPO's pessimistic surrogate (L^CLIP)
    actor_loss -= 1e-3 * tf.reduce_mean(dist.entropy())

    update_policy_old = tf.group(*[
        tf.assign(old_var, var)
        for var, old_var in zip(tf.global_variables('policy/'), tf.global_variables('policy_old/'))])

    # training
    loss = actor_loss + tf.losses.get_regularization_loss()

    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops):
        train_step = tf.train.AdamOptimizer(args.learning_rate).minimize(loss, global_step=global_step)

    # summary
    ep_length = tf.placeholder(tf.float32, [])
    ep_reward = tf.placeholder(tf.float32, [])
    metrics, update_metrics = {}, {}
    metrics['loss'], update_metrics['loss'] = tf.metrics.mean(loss)
    metrics['ep_length'], update_metrics['ep_length'] = tf.metrics.mean(ep_length)
    metrics['ep_reward'], update_metrics['ep_reward'] = tf.metrics.mean(ep_reward)
    summary = tf.summary.merge([
        tf.summary.scalar('loss', metrics['loss']),
        tf.summary.scalar('ep_length', metrics['ep_length']),
        tf.summary.scalar('ep_reward', metrics['ep_reward'])
    ])

    locals_init = tf.local_variables_initializer()
    saver = tf.train.Saver()
    with tf.Session() as sess, tf.summary.FileWriter(experiment_path) as writer:
        if tf.train.latest_checkpoint(experiment_path):
            saver.restore(sess, tf.train.latest_checkpoint(experiment_path))
        else:
            sess.run(tf.global_variables_initializer())
        sess.run(locals_init)

        for ep in tqdm(range(args.episodes), desc='training'):
            history = []
            s = env.reset()
            ep_r = 0

            for t in itertools.count():
                a = sess.run(action_sample, {state: s.reshape((1, state_size)), training: False}).squeeze(0)
                s_prime, r, d, _ = env.step(a)
                ep_r += r

                history.append((s, a, r))

                if d:
                    break
                else:
                    s = s_prime

            batch = utils.discounted_return(history, args.gamma)

            _, _, step = sess.run(
                [train_step, update_metrics, global_step],
                {
                    state: batch[0],
                    action: batch[1],
                    ret: batch[2],
                    ep_length: t,
                    ep_reward: ep_r,
                    training: True,
                })

            sess.run(update_policy_old)

            if ep % 100 == 0:
                summ, metr = sess.run([summary, metrics])
                writer.add_summary(summ, step)
                writer.flush()
                saver.save(sess, os.path.join(experiment_path, 'model.ckpt'))
                sess.run(locals_init)


if __name__ == '__main__':
    main()

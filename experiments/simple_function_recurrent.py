
import os
import json
import torch
import stable_nalu
import itertools
import multiprocessing

use_cuda = torch.cuda.is_available()

def make_batch_dataset(operation, batch_size=128, num_workers=0, use_cuda=False, **kwargs):
    dataset = stable_nalu.dataset.SimpleFunctionRecurrentDataset(
        operation=operation, num_workers=num_workers, **kwargs)
    batcher = torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        sampler=torch.utils.data.SequentialSampler(dataset),
        num_workers=num_workers,
        worker_init_fn=dataset.worker_init_fn)

    if use_cuda:
        return map(lambda args: (arg.cuda() for arg in args), batcher)
    else:
        return batcher


layer_types = [
    'RNN-tanh',
    'RNN-ReLU',
    'GRU',
    'LSTM',
    'NAC',
    'NALU'
]

operations = [
    'add',
    'sub',
    'mul',
    'div',
    'squared',
    'root'
]

seeds = range(1)

num_workers = min(8, multiprocessing.cpu_count())
loss_diff_running_mean_memory = 0.99
loss_diff_threshold = 0.5
max_iterations = 100000

os.makedirs("results", exist_ok=True)
fp = open('results/simple_function_recurrent.ndjson', 'w')

for layer_type, operation, seed in itertools.product(
    layer_types, operations, seeds
):
    print(f'running layer_type: {layer_type}, operation: {operation}, seed: {seed}')

    writer = stable_nalu.writer.SummaryWriter(
        log_dir=f'runs/recurrent/{layer_type.lower()}_{operation.lower()}_{seed}')

    # Setup datasets
    dataset_train = iter(make_batch_dataset(
        operation, input_range=1, time_length=10,
        batch_size=128,
        seed=seed * 3 * num_workers + 0 * num_workers,
        use_cuda=use_cuda, num_workers=num_workers))
    dataset_valid_interpolation = iter(make_batch_dataset(
        operation, input_range=1, time_length=10,
        batch_size=2048,
        seed=seed * 3 * num_workers + 1 * num_workers,
        use_cuda=use_cuda, num_workers=num_workers))
    dataset_valid_extrapolation = iter(make_batch_dataset(
        operation, input_range=1, time_length=1000,
        batch_size=2048,
        seed=seed * 3 * num_workers + 2 * num_workers,
        use_cuda=use_cuda, num_workers=num_workers))

    # setup model
    model = stable_nalu.network.SimpleFunctionRecurrentNetwork(layer_type)
    if use_cuda:
        model.cuda()
    torch.manual_seed(seed)
    model.reset_parameters()
    criterion = torch.nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters())

    # Train model
    previous_loss = None
    loss_diff_running_mean = None
    for epoch_i, (x_train, t_train) in zip(range(max_iterations + 1), dataset_train):
        writer.set_iteration(epoch_i)

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward
        y_train = model(x_train)
        loss_train = criterion(y_train, t_train)

        # Do running mean of diff(loss) to stop training before max-epoch
        stop_training = False
        loss_train_value = loss_train.detach().cpu().numpy().item(0)
        if previous_loss is not None:
            loss_diff = abs(previous_loss - loss_train_value)

            if loss_diff_running_mean is None:
                loss_diff_running_mean = loss_diff
            else:
                loss_diff_running_mean = (
                    loss_diff_running_mean_memory * loss_diff_running_mean +
                    (1 - loss_diff_running_mean_memory) * loss_diff
                )

        # stop training if diff(loss) is small
        if (loss_diff_running_mean is not None and
                loss_diff_running_mean < loss_diff_threshold):
            stop_training = True

        # Log loss
        writer.add_scalar('loss/train', loss_train)
        if epoch_i % 100 == 0 or stop_training:
            x_valid_inter, t_valid_inter = next(dataset_valid_interpolation)
            loss_valid_inter = criterion(model(x_valid_inter), t_valid_inter)
            writer.add_scalar('loss/valid/interpolation', loss_valid_inter)

            x_valid_extra, t_valid_extra = next(dataset_valid_extrapolation)
            loss_valid_extra = criterion(model(x_valid_extra), t_valid_extra)
            writer.add_scalar('loss/valid/extrapolation', loss_valid_extra)

        if epoch_i % 1000 == 0 or stop_training:
            print(f'  {epoch_i}: {loss_train_value}')

        # Backward + optimize model
        if not stop_training:
            loss_train.backward()
            optimizer.step()

        # save loss for next iteration
        previous_loss = loss_train_value
        # early stop based on diff(training loss)
        if stop_training:
            break

    # Write results for this training
    print(f'  finished:')
    print(f'  - epochs: {epoch_i}')
    print(f'  - loss_train: {loss_train}')
    print(f'  - loss_valid_inter: {loss_valid_inter}')
    print(f'  - loss_valid_extra: {loss_valid_extra}')
    fp.write(json.dumps({
        'layer_type': layer_type,
        'operation': operation,
        'seed': seed,
        'epochs': epoch_i,
        'loss_train': loss_train.detach().cpu().numpy().item(0),
        'loss_valid_inter': loss_valid_inter.detach().cpu().numpy().item(0),
        'loss_valid_extra': loss_valid_extra.detach().cpu().numpy().item(0)
    }) + '\n')

    writer.close()

fp.close()
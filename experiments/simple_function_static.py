
import math
import torch
import stable_nalu
import argparse

# Parse arguments
parser = argparse.ArgumentParser(description='Runs the simple function static task')
parser.add_argument('--layer-type',
                    action='store',
                    default='NALU',
                    choices=[
                        'Tanh', 'Sigmoid', 'ReLU6', 'Softsign', 'SELU',
                        'ELU', 'ReLU', 'linear', 'GumbelNAC', 'NAC', 'GumbelNALU', 'NALU'
                    ],
                    type=str,
                    help='Specify the layer type, e.g. Tanh, ReLU, NAC, NALU')
parser.add_argument('--operation',
                    action='store',
                    default='add',
                    choices=[
                        'add', 'sub', 'mul', 'div', 'squared', 'root'
                    ],
                    type=str,
                    help='Specify the operation to use, e.g. add, mul, squared')
parser.add_argument('--seed',
                    action='store',
                    default=0,
                    type=int,
                    help='Specify the seed to use')
parser.add_argument('--max-iterations',
                    action='store',
                    default=100000,
                    type=int,
                    help='Specify the max number of iterations to use')
parser.add_argument('--cuda',
                    action='store',
                    default=torch.cuda.is_available(),
                    type=bool,
                    help='Should CUDA be used')
parser.add_argument('--verbose',
                    action='store_true',
                    default=False,
                    help='Should network measures (e.g. gates) and gradients be shown')
args = parser.parse_args()

# Print configuration
print(f'running')
print(f'  - seed: {args.seed}')
print(f'  - operation: {args.operation}')
print(f'  - layer_type: {args.layer_type}')
print(f'  - cuda: {args.cuda}')
print(f'  - verbose: {args.verbose}')
print(f'  - max_iterations: {args.max_iterations}')

# Prepear logging
results_writer = stable_nalu.writer.ResultsWriter('simple_function_static')
summary_writer = stable_nalu.writer.SummaryWriter(
    f'simple_function_static/{args.layer_type.lower()}_{args.operation.lower()}_{args.seed}'
)

# Set seed
torch.manual_seed(args.seed)

# Setup datasets
dataset = stable_nalu.dataset.SimpleFunctionStaticDataset(
    operation=args.operation,
    use_cuda=args.cuda,
    seed=args.seed
)
dataset_train = iter(dataset.fork(input_range=1).dataloader(batch_size=128))
dataset_valid_interpolation = iter(dataset.fork(input_range=1).dataloader(batch_size=2048))
dataset_valid_extrapolation = iter(dataset.fork(input_range=5).dataloader(batch_size=2048))

# setup model
model = stable_nalu.network.SimpleFunctionStaticNetwork(
    args.layer_type,
    writer=summary_writer if args.verbose else stable_nalu.writer.DummyWriter()
)
if args.cuda:
    model.cuda()
model.reset_parameters()
criterion = torch.nn.MSELoss()
optimizer = torch.optim.Adam(model.parameters())

# Collect temperatures
parameter_tau = []
for name, parameter in model.named_parameters():
    if (name[-4:] == '.tau'):
        parameter_tau.append(parameter)

# Train model
for epoch_i, (x_train, t_train) in zip(range(args.max_iterations + 1), dataset_train):
    summary_writer.set_iteration(epoch_i)

    # zero the parameter gradients
    optimizer.zero_grad()

    # Set temperature
    for tau in parameter_tau:
        tau.fill_(max(0.5, math.exp(-1e-5 * epoch_i)))

    # forward
    y_train = model(x_train)
    loss_train = criterion(y_train, t_train)

    # Log loss
    summary_writer.add_scalar('loss/train', loss_train)
    if epoch_i % 1000 == 0:
        with torch.no_grad():
            x_valid_inter, t_valid_inter = next(dataset_valid_interpolation)
            loss_valid_inter = criterion(model(x_valid_inter), t_valid_inter)
            summary_writer.add_scalar('loss/valid/interpolation', loss_valid_inter)

            x_valid_extra, t_valid_extra = next(dataset_valid_extrapolation)
            loss_valid_extra = criterion(model(x_valid_extra), t_valid_extra)
            summary_writer.add_scalar('loss/valid/extrapolation', loss_valid_extra)

        print(f'train {epoch_i}: {loss_train}')

    # Backward + optimize model
    loss_train.backward()
    optimizer.step()

    # Log gradients if in verbose mode
    if args.verbose and epoch_i % 1000 == 0:
        for name, weight in model.named_parameters():
            if weight.requires_grad:
                gradient, *_ = weight.grad.data
                summary_writer.add_summary(f'grad/{name}', gradient)

# Write results for this training
print(f'finished:')
print(f'  - loss_train: {loss_train}')
print(f'  - loss_valid_inter: {loss_valid_inter}')
print(f'  - loss_valid_extra: {loss_valid_extra}')

# save results
results_writer.add({
    'seed': args.seed,
    'operation': args.operation,
    'layer_type': args.layer_type,
    'cuda': args.cuda,
    'verbose': args.verbose,
    'max_iterations': args.max_iterations,
    'loss_train': loss_train,
    'loss_valid_inter': loss_valid_inter,
    'loss_valid_extra': loss_valid_extra
})

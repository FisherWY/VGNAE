import os.path as osp
import argparse

import torch
import torch.nn as nn
import torch.nn.functional as F

from torch_geometric.datasets import Planetoid, Coauthor, Amazon
from torch_geometric.utils import train_test_split_edges
from torch_geometric.nn import GAE, VGAE, APPNP
import torch_geometric.transforms as T

from converter import read_data_from_npz

parser = argparse.ArgumentParser()
parser.add_argument('--model', type=str, default='VGNAE')
parser.add_argument('--dataset', type=str, default='Cora')
parser.add_argument('--epochs', type=int, default=300)
parser.add_argument('--channels', type=int, default=128)
parser.add_argument('--scaling_factor', type=float, default=1.8)
parser.add_argument('--training_rate', type=float, default=0.8) 
args = parser.parse_args()

path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data', args.dataset)

if args.dataset in ['Cora', 'CiteSeer', 'PubMed']:
    dataset = Planetoid(path, args.dataset, 'public')
if args.dataset in ['cs', 'physics']:
    dataset = Coauthor(path, args.dataset, 'public')
if args.dataset in ['computers', 'photo']:
    dataset = Amazon(path, args.dataset, 'public')
if args.dataset in ['fb_1684', 'fb_1912', 'mag_cs', 'mag_eng']:
    dataset = read_data_from_npz(path, args.dataset)

data = dataset[0]
data = T.NormalizeFeatures()(data)


class Encoder(torch.nn.Module):
    def __init__(self, in_channels, out_channels, edge_index):
        super(Encoder, self).__init__()
        self.linear1 = nn.Linear(in_channels, out_channels)
        self.linear2 = nn.Linear(in_channels, out_channels)
        self.propagate = APPNP(K=1, alpha=0)

    def forward(self, x, edge_index, not_prop=0):
        if args.model == 'GNAE':
            x = self.linear1(x)
            x = F.normalize(x, p=2, dim=1) * args.scaling_factor
            x = self.propagate(x, edge_index)
            return x

        if args.model == 'VGNAE':
            x_ = self.linear1(x)
            x_ = self.propagate(x_, edge_index)

            x = self.linear2(x)
            x = F.normalize(x, p=2, dim=1) * args.scaling_factor
            x = self.propagate(x, edge_index)
            return x, x_

        return x


dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
channels = args.channels
train_rate = args.training_rate
val_ratio = (1-args.training_rate) / 3
test_ratio = (1-args.training_rate) / 3 * 2
data = train_test_split_edges(data.to(dev), val_ratio=val_ratio, test_ratio=test_ratio)

N = int(data.x.size()[0])
if args.model == 'GNAE':
    model = GAE(Encoder(data.x.size()[1], channels, data.train_pos_edge_index)).to(dev)
if args.model == 'VGNAE':
    model = VGAE(Encoder(data.x.size()[1], channels, data.train_pos_edge_index)).to(dev)

data.train_mask = data.val_mask = data.test_mask = data.y = None
x, train_pos_edge_index = data.x.to(dev), data.train_pos_edge_index.to(dev)
optimizer = torch.optim.Adam(model.parameters(), lr=0.005)


def train():
    model.train()
    optimizer.zero_grad()
    z = model.encode(x, train_pos_edge_index)
    loss = model.recon_loss(z, train_pos_edge_index)
    if args.model == 'VGNAE':
        loss = loss + (1 / data.num_nodes) * model.kl_loss()
    loss.backward()
    optimizer.step()
    return loss


def test(pos_edge_index, neg_edge_index, plot_his=0):
    model.eval()
    with torch.no_grad():
        z = model.encode(x, train_pos_edge_index)
    return model.test(z, pos_edge_index, neg_edge_index)


def predict(pred_edge_index):
    model.eval()
    with torch.no_grad():
        z = model.encode(x, train_pos_edge_index)
        pred = model.decode(z, pred_edge_index, sigmoid=True)
        return pred


best_epochs = 0
best_loss = 10
best_auc = 0.0
best_ap = 0.0
for epoch in range(1, args.epochs):
    loss = train()
    loss = float(loss)
    
    with torch.no_grad():
        test_pos, test_neg = data.test_pos_edge_index, data.test_neg_edge_index
        auc, ap = test(data.test_pos_edge_index, data.test_neg_edge_index)
        print('Epoch: {:03d}, LOSS: {:.4f}, AUC: {:.4f}, AP: {:.4f}'.format(epoch, loss, auc, ap))
        if loss < best_loss:
            best_epochs = epoch
            best_auc = auc
            best_ap = ap
            best_loss = loss

print('Best: Epoch: {:03d}, LOSS: {:.4f}, AUC: {:.4f}, AP: {:.4f}'.format(best_epochs, best_loss, best_auc, best_ap))
# torch.save(model.state_dict(), './%s_%s_epochs%d_training-rate%d.pt' % (args.model, args.dataset, args.epochs, int(args.training_rate * 100)))

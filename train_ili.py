import numpy as np
import torch
import torch.optim as optim
from models.utils import float_tensor, device
import pickle
from models.fnpmodels import EmbedAttenSeq, RegressionFNP, EmbedSeq, RegressionFNP2
import matplotlib.pyplot as plt
import pandas as pd
from optparse import OptionParser
import os


for d in ["model_chkp", "plots", "saves"]:
    if not os.path.exists(d):
        os.mkdir(d)


np.random.seed(10)

city_idx = {f"Region {i}": i for i in range(1, 11)}
city_idx["X"] = 0

df = pd.read_csv("./data/ILINet.csv")
df = df[["REGION", "YEAR", "WEEK", "% WEIGHTED ILI"]]
df = df[(df["YEAR"] >= 2004) | ((df["YEAR"] == 2003) & (df["WEEK"] >= 20))]


def get_dataset(year: int, region: str, df=df):
    ans = df[
        ((df["YEAR"] == year) & (df["WEEK"] >= 20))
        | ((df["YEAR"] == year + 1) & (df["WEEK"] <= 20))
    ]
    return ans[ans["REGION"] == region]["% WEIGHTED ILI"]


parser = OptionParser()
parser.add_option("-y", "--year", dest="testyear", type="int")
parser.add_option("-w", "--week", dest="week_ahead", type="int")
parser.add_option("-a", "--atten", dest="atten", type="string")
parser.add_option("-n", "--num", dest="num", type="string")
parser.add_option("-e", "--epoch", dest="epochs", type="int")
(options, args) = parser.parse_args()

train_seasons = list(range(2003, options.testyear))
test_seasons = [options.testyear]
# train_seasons = list(range(2003, 2019))
# test_seasons = [2019]
print(train_seasons, test_seasons)

# train_seasons = [2003, 2004, 2005, 2006, 2007, 2008, 2009]
# test_seasons = [2010]
regions = ["X"]
# regions = [f"Region {i}" for i in range(1,11)]

week_ahead = options.week_ahead
val_frac = 5
attn = options.atten
model_num = options.num
# model_num = 22
EPOCHS = options.epochs
print(week_ahead, attn, EPOCHS)


def one_hot(idx, dim=len(city_idx)):
    ans = np.zeros(dim, dtype="float32")
    ans[idx] = 1.0
    return ans


def save_data(obj, filepath):
    with open(filepath, "wb") as fl:
        pickle.dump(obj, fl)


full_x = np.array(
    [
        np.array(get_dataset(s, r), dtype="float32")[-53:]
        for s in train_seasons
        for r in regions
    ]
)
full_meta = np.array([one_hot(city_idx[r]) for s in train_seasons for r in regions])
full_y = full_x.argmax(-1)
full_x = full_x[:, :, None]

full_x_test = np.array(
    [
        np.array(get_dataset(s, r), dtype="float32")[-53:]
        for s in test_seasons
        for r in regions
    ]
)
full_meta_test = np.array([one_hot(city_idx[r]) for s in test_seasons for r in regions])
full_y_test = full_x_test.argmax(-1)
full_x_test = full_x_test[:, :, None]


def create_dataset(full_meta, full_x, week_ahead=week_ahead):
    metas, seqs, y = [], [], []
    for meta, seq in zip(full_meta, full_x):
        for i in range(20, full_x.shape[1]):
            metas.append(meta)
            seqs.append(seq[: i - week_ahead + 1])
            y.append(seq[i])
    return np.array(metas, dtype="float32"), seqs, np.array(y, dtype="float32")

# meta is region one hot encoding: [no of obs, no of regions]
# x is snippet of time series: [no of obs, size of full sequence]
# y is k-week ahead target: [no of obs, target size]
train_meta, train_x, train_y = create_dataset(full_meta, full_x)
test_meta, test_x, test_y = create_dataset(full_meta_test, full_x_test)


def create_tensors(metas, seqs, ys):
    metas = float_tensor(metas)
    ys = float_tensor(ys)
    max_len = max([len(s) for s in seqs])
    out_seqs = np.zeros((len(seqs), max_len, seqs[0].shape[-1]), dtype="float32")
    lens = np.zeros(len(seqs), dtype="int32")
    for i, s in enumerate(seqs):
        out_seqs[i, : len(s), :] = s
        lens[i] = len(s)
    out_seqs = float_tensor(out_seqs)
    return metas, out_seqs, ys, lens


def create_mask1(lens, out_dim=1):
    ans = np.zeros((max(lens), len(lens), out_dim), dtype="float32")
    for i, j in enumerate(lens):
        ans[j - 1, i, :] = 1.0
    return float_tensor(ans)


def create_mask(lens, out_dim=1):
    ans = np.zeros((max(lens), len(lens), out_dim), dtype="float32")
    for i, j in enumerate(lens):
        ans[:j, i, :] = 1.0
    return float_tensor(ans)


if attn == "trans":
    emb_model = EmbedAttenSeq(
        dim_seq_in=1,
        dim_metadata=len(city_idx),
        dim_out=50,
        n_layers=2,
        bidirectional=True,
    ).cuda()
    emb_model_full = EmbedAttenSeq(
        dim_seq_in=1,
        dim_metadata=len(city_idx),
        dim_out=50,
        n_layers=2,
        bidirectional=True,
    ).cuda()
else:
    emb_model = EmbedSeq(
        dim_seq_in=1,
        dim_metadata=len(city_idx),
        dim_out=50,
        n_layers=2,
        bidirectional=True,
    ).cuda()
    emb_model_full = EmbedSeq(
        dim_seq_in=1,
        dim_metadata=len(city_idx),
        dim_out=50,
        n_layers=2,
        bidirectional=True,
    ).cuda()
fnp_model = RegressionFNP2(
    dim_x=50,
    dim_y=1,
    dim_h=100,
    n_layers=3,
    num_M=train_meta.shape[0],
    dim_u=50,
    dim_z=50,
    fb_z=0.0,
    use_ref_labels=False,
    use_DAG=False,
    add_atten=False,
).cuda()
optimizer = optim.Adam(
    list(emb_model.parameters())
    + list(fnp_model.parameters())
    + list(emb_model_full.parameters()),
    lr=1e-3,
)

# emb_model_full = emb_model

train_meta_, train_x_, train_y_, train_lens_ = create_tensors(
    train_meta, train_x, train_y
)

test_meta, test_x, test_y, test_lens = create_tensors(test_meta, test_x, test_y)

# full_x_chunks = np.zeros((full_x.shape[0] * 4, full_x.shape[1], full_x.shape[2]))
# full_meta_chunks = np.zeros((full_meta.shape[0] * 4, full_meta.shape[1]))
# for i, s in enumerate(full_x):
#     full_x_chunks[i * 4, -20:] = s[:20]
#     full_x_chunks[i * 4 + 1, -30:] = s[:30]
#     full_x_chunks[i * 4 + 2, -40:] = s[:40]
#     full_x_chunks[i * 4 + 3, :] = s
#     full_meta_chunks[i * 4 : i * 4 + 4] = full_meta[i]

full_x = float_tensor(full_x)
full_meta = float_tensor(full_meta)
full_y = float_tensor(full_y)

train_mask_, test_mask = (
    create_mask(train_lens_),
    create_mask(test_lens),
)

# perm = np.random.permutation(train_meta_.shape[0])
# val_perm = perm[: train_meta_.shape[0] // val_frac]
# train_perm = perm[train_meta_.shape[0] // val_frac :]

# train_meta, train_x, train_y, train_lens, train_mask = (
#     train_meta_[train_perm],
#     train_x_[train_perm],
#     train_y_[train_perm],
#     train_lens_[train_perm],
#     train_mask_[:, train_perm, :],
# )
# val_meta, val_x, val_y, val_lens, val_mask = (
#     train_meta_[val_perm],
#     train_x_[val_perm],
#     train_y_[val_perm],
#     train_lens_[val_perm],
#     train_mask_[:, val_perm, :],
# )


def save_model(file_prefix: str):
    torch.save(emb_model.state_dict(), file_prefix + "_emb_model.pth")
    torch.save(emb_model_full.state_dict(), file_prefix + "_emb_model_full.pth")
    torch.save(fnp_model.state_dict(), file_prefix + "_fnp_model.pth")


def load_model(file_prefix: str):
    emb_model.load_state_dict(torch.load(file_prefix + "_emb_model.pth"))
    emb_model_full.load_state_dict(torch.load(file_prefix + "_emb_model_full.pth"))
    fnp_model.load_state_dict(torch.load(file_prefix + "_fnp_model.pth"))


def evaluate(sample=True, dtype="test"):
    with torch.no_grad():
        emb_model.eval()
        emb_model_full.eval()
        fnp_model.eval()
        full_embeds = emb_model_full(full_x.transpose(1, 0), full_meta)
        if dtype == "val":
            x_embeds = emb_model.forward_mask(val_x.transpose(1, 0), val_meta, val_mask)
        elif dtype == "test":
            x_embeds = emb_model.forward_mask(
                test_x.transpose(1, 0), test_meta, test_mask
            )
        elif dtype == "train":
            x_embeds = emb_model.forward_mask(
                train_x.transpose(1, 0), train_meta, train_mask
            )
        elif dtype == "all":
            x_embeds = emb_model.forward_mask(
                train_x_.transpose(1, 0), train_meta_, train_mask_
            )
        else:
            raise ValueError("Incorrect dtype")
        y_pred, _, vars, _, _, _, _ = fnp_model.predict(
            x_embeds, full_embeds, full_y, sample=sample
        )
    labels_dict = {"val": val_y, "test": test_y, "train": train_y, "all": train_y_}
    labels = labels_dict[dtype]
    mse_error = torch.pow(y_pred - labels, 2).mean().sqrt().detach().cpu().numpy()
    return (
        mse_error,
        y_pred.detach().cpu().numpy().ravel(),
        labels.detach().cpu().numpy().ravel(),
        vars.mean().detach().cpu().numpy().ravel(),
        full_embeds.detach().cpu().numpy(),
        x_embeds.detach().cpu().numpy(),
    )


error = 100.0
losses = []
errors = []
train_errors = []
variances = []
best_ep = 0

for ep in range(EPOCHS):
    emb_model.train()
    emb_model_full.train()
    fnp_model.train()
    print(f"Epoch: {ep+1}")
    optimizer.zero_grad()
    x_embeds = emb_model.forward_mask(train_x.transpose(1, 0), train_meta, train_mask)
    full_embeds = emb_model_full(full_x.transpose(1, 0), full_meta)
    loss, yp, _ = fnp_model.forward(full_embeds, full_y, x_embeds, train_y)
    # calculate Z values only for a sample of weeks, say 20, 30, 40, 50
    # input of Z is yp, 
    loss.backward()
    optimizer.step()
    losses.append(loss.detach().cpu().numpy())
    train_errors.append(
        torch.pow(yp[full_x.shape[0] :] - train_y, 2)
        .mean()
        .sqrt()
        .detach()
        .cpu()
        .numpy()
    )

    e, yp, yt, _, _, _ = evaluate(False)
    e = np.mean([evaluate(True, dtype="val")[0] for _ in range(40)])
    vars = np.mean([evaluate(True, dtype="val")[3] for _ in range(40)])
    errors.append(e)
    variances.append(vars)
    idxs = np.random.randint(yp.shape[0], size=10)
    print("Loss:", loss.detach().cpu().numpy())
    print(f"Val RMSE: {e:.3f}, Train RMSE: {train_errors[-1]:.3f}")
    # print(f"MSE: {e}")
    if ep > 100 and min(errors[-100:]) > error + 0.1:
        errors = errors[: best_ep + 1]
        losses = losses[: best_ep + 1]
        print(f"Done in {ep+1} epochs")
        break
    if e < error:
        save_model(f"model_chkp/model{model_num}")
        error = e
        best_ep = ep + 1


print(f"Val MSE error: {error}")
plt.figure(1)
plt.plot(losses)
plt.savefig(f"plots/losses{model_num}.png")
plt.figure(2)
plt.plot(errors)
plt.plot(train_errors)
plt.savefig(f"plots/errors{model_num}.png")
plt.figure(3)
plt.plot(variances)
plt.savefig(f"plots/vars{model_num}.png")

load_model(f"model_chkp/model{model_num}")

e, yp, yt, vars, fem, tem = evaluate(True)
yp = np.array([evaluate(True)[1] for _ in range(1000)])
yp, vars = np.mean(yp, 0), np.var(yp, 0)
e = np.mean((yp - yt) ** 2)
dev = np.sqrt(vars) * 1.95
plt.figure(4)
plt.plot(yp, label="Predicted 95%", color="blue")
plt.fill_between(np.arange(len(yp)), yp + dev, yp - dev, color="blue", alpha=0.2)
plt.plot(yt, label="True Value", color="green")
plt.legend()
plt.title(f"RMSE: {e}")
plt.savefig(f"plots/Test{model_num}.png")
dt = {
    "rmse": e,
    "target": yt,
    "pred": yp,
    "vars": vars,
    "fem": fem,
    "tem": tem,
}
save_data(dt, f"./saves/{model_num}_test.pkl")

e, yp, yt, vars, _, _ = evaluate(True, dtype="val")
yp = np.array([evaluate(True, dtype="val")[1] for _ in range(1000)])
yp, vars = np.mean(yp, 0), np.var(yp, 0)
e = np.mean((yp - yt) ** 2)
dev = np.sqrt(vars) * 1.95
plt.figure(5)
plt.plot(yp, label="Predicted 95%", color="blue")
plt.fill_between(np.arange(len(yp)), yp + dev, yp - dev, color="blue", alpha=0.2)
plt.plot(yt, label="True Value", color="green")
plt.legend()
plt.title(f"RMSE: {e}")
plt.savefig(f"plots/Val{model_num}.png")

e, yp, yt, vars, fem, tem = evaluate(True, dtype="all")
yp = np.array([evaluate(True, dtype="all")[1] for _ in range(40)])
yp, vars = np.mean(yp, 0), np.var(yp, 0)
e = np.mean((yp - yt) ** 2)
dev = np.sqrt(vars) * 1.95
plt.figure(6)
plt.plot(yp, label="Predicted 95%", color="blue")
plt.fill_between(np.arange(len(yp)), yp + dev, yp - dev, color="blue", alpha=0.2)
plt.plot(yt, label="True Value", color="green")
plt.legend()
plt.title(f"RMSE: {e}")
plt.savefig(f"plots/Train{model_num}.png")
dt = {
    "rmse": e,
    "target": yt,
    "pred": yp,
    "vars": vars,
    "fem": fem,
    "tem": tem,
}
save_data(dt, f"./saves/{model_num}_train.pkl")

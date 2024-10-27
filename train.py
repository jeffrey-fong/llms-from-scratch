import argparse
import datetime
import json
import math
from typing import Dict, List

import torch
from torch.utils.data import DataLoader, Dataset
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from models.transformer.transformer import Transformer
from utils.tokenizer import Tokenizer


class GutenbergPoetryDataset(Dataset):
    def __init__(self, seq_len: int, tokenizer: Tokenizer):
        with open("gutenberg_poetry_corpus.ndjson", "r") as file:
            self.data = [json.loads(line.strip())["s"] for line in file]
        self.data = tokenizer.encode("".join(self.data))
        self.labels = self.data[1:] + [0]
        # Split data into sequences of length seq_len
        self.data = [
            self.data[i : min(i + seq_len, len(self.data))]
            for i in range(0, len(self.data), seq_len)
        ]
        self.labels = [
            self.labels[i : min(i + seq_len, len(self.labels))]
            for i in range(0, len(self.labels), seq_len)
        ]
        # Pad the last sequence if necessary
        if len(self.data[-1]) < seq_len:
            self.data[-1] = self.data[-1] + [0] * (seq_len - len(self.data[-1]))
        if len(self.labels[-1]) < seq_len:
            self.labels[-1] = self.labels[-1] + [0] * (seq_len - len(self.labels[-1]))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return (
            torch.tensor(self.data[idx], dtype=torch.long),
            torch.tensor(self.labels[idx], dtype=torch.long),
        )


def cosine_lr_schedule(step: int, max_steps: int) -> float:
    min_lr = min(args.lr / 5, 1e-4)

    # Warmup phase
    if step < args.warmup_steps:
        return (float(step) / float(max(1, args.warmup_steps))) * args.lr

    # Cosine decay phase
    progress = float(step - args.warmup_steps) / float(
        max(1, max_steps - args.warmup_steps)
    )
    return min_lr + 0.5 * (args.lr - min_lr) * (1.0 + math.cos(math.pi * progress))


def train(tokenizer: Tokenizer, model: Transformer):
    dataset = GutenbergPoetryDataset(args.seq_len, tokenizer)
    train_data, val_data = torch.utils.data.random_split(
        dataset, [args.train_ratio, 1 - args.train_ratio]
    )
    train_dataloader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True)
    val_dataloader = DataLoader(val_data, batch_size=args.batch_size, shuffle=True)

    max_steps = len(train_dataloader) * args.epochs

    date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    writer = SummaryWriter(f"runs/{args.model_type}_{date}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        opt, lr_lambda=lambda step: cosine_lr_schedule(step, max_steps)
    )

    for epoch in tqdm(range(args.epochs), desc="Epochs"):
        model.train()
        progress_bar = tqdm(train_dataloader, desc=f"Training epoch {epoch + 1}")
        train_loss = 0.0
        for step, (X, y) in enumerate(progress_bar):
            # Forward pass
            X, y = X.to(args.device), y.to(args.device)
            logits, loss = model(X, y)

            # Backward pass
            loss.backward()
            opt.step()
            scheduler.step()
            opt.zero_grad()
            progress_bar.set_postfix({"train_loss": loss.item()})

            # Log training metrics
            train_loss += loss.item()
            progress_bar.set_postfix({"train_loss": loss.item()})
            curr_step = epoch * len(train_dataloader) + step
            writer.add_scalar("Loss/train_step", loss.item(), curr_step)
            writer.add_scalar("Learning_rate", scheduler.get_last_lr()[0], curr_step)

        # Log average training loss for epoch
        avg_train_loss = train_loss / len(train_dataloader)
        writer.add_scalar("Loss/train_epoch", avg_train_loss, epoch)

        # Validation
        model.eval()
        val_loss = 0.0
        val_progress = tqdm(val_dataloader, desc=f"Validation epoch {epoch + 1}")
        with torch.no_grad():
            for X, y in val_progress:
                X, y = X.to(args.device), y.to(args.device)
                _, loss = model(X, y)
                val_loss += loss

        # Log validation metrics
        avg_val_loss = val_loss / len(val_dataloader)
        writer.add_scalar("Loss/validation", avg_val_loss, epoch)
        print(f"Validation loss: {avg_val_loss}")

    writer.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train different language model architectures"
    )

    # Data Arguments
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.9,
        help="Ratio of the training data",
    )

    # Model Arguments
    parser.add_argument(
        "--model-type",
        type=str,
        default="transformer",
        choices=["transformer"],
        help="Model type (currently only transformer is supported)",
    )
    parser.add_argument(
        "--seq-len", type=int, default=128, help="Max sequence length of the model"
    )
    parser.add_argument(
        "--device", type=str, default="cpu", help="Device to use for training"
    )

    # Training Arguments
    parser.add_argument("--lr", type=float, default=0.002, help="Learning rate")
    parser.add_argument("--warmup-steps", type=int, default=10, help="Warmup steps")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size")
    parser.add_argument("--epochs", type=int, default=1, help="Number of epochs")
    return parser.parse_args()


def main():
    tokenizer = Tokenizer()
    if args.model_type == "transformer":
        model = Transformer(
            seq_len=args.seq_len,
            vocab_size=tokenizer.vocab_size,
            device=args.device,
        ).to(args.device)
    else:
        raise ValueError(f"Model type {args.model_type} not supported")

    train(tokenizer, model)


if __name__ == "__main__":
    torch.manual_seed(10)
    args = parse_args()
    main()
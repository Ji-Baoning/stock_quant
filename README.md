# Stock Quant

An engineering-validation MVP for reproducible A-share quantitative research.

The first phase uses fixed boundary samples, daily market data, a 60-trading-day
momentum baseline, and three cost scenarios. It is not investment advice, a
profitability claim, or a live-trading system.

## Setup

```bash
conda env create -f environment.yml
conda activate stock-quant
```

Copy `.env.example` to `.env` and replace its placeholder only when using the
Tushare source. Do not commit `.env` or credentials.

## Configuration

Project, source, and cost settings are stored under `configs/`. Tushare's token
is read only from the `TUSHARE_TOKEN` environment variable and is never stored
in configuration files.

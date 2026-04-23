from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from openai import AzureOpenAI
import os
import requests
from bs4 import BeautifulSoup

# ============================
# 初期化
# ============================
load_dotenv()
app = FastAPI()

# ============================
# Azure OpenAI クライアント
# ============================
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

AZURE_MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")  # 例: gpt-4o-mini

# ============================
# summary の整形
# ============================
def format_summary_value(x):
    if isinstance(x, str) and "%" in x:
        return x
    if isinstance(x, (int, float)):
        return f"{x:,}"
    return x

# ============================
# ファンダメンタル取得
# ============================
def get_fundamentals(ticker: str):
    t = yf.Ticker(ticker)
    try:
        raw = t.info
    except Exception:
        return {"ticker": ticker, "error": "info not available"}

    def g(key, default=None):
        return raw.get(key, default)

    return {
        "ticker": ticker,
        "shortName": g("shortName"),
        "sector": g("sector"),
        "industry": g("industry"),
        "marketCap": g("marketCap"),
        "trailingPE": g("trailingPE"),
        "forwardPE": g("forwardPE"),
        "beta": g("beta"),
        "revenueGrowth": g("revenueGrowth"),
        "grossMargins": g("grossMargins"),
        "operatingMargins": g("operatingMargins"),
        "profitMargins": g("profitMargins"),
        "targetMeanPrice": g("targetMeanPrice"),
        "fiftyTwoWeekHigh": g("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": g("fiftyTwoWeekLow"),
    }

# ============================
# 市場データ取得
# ============================
def get_market_snapshot():
    tickers = {
        "nikkei_225": "^N225",
        "topix": "^TOPX",
        "usd_jpy": "JPY=X",
        "us10y_yield": "^TNX",
        "vix": "^VIX",
    }
    data = {}
    for name, code in tickers.items():
        try:
            price = yf.Ticker(code).history(period="1d")["Close"].iloc[-1]
        except Exception:
            price = None
        data[name] = price
    return pd.DataFrame([data])

# ============================
# ポートフォリオ分析（Gradio版の完全移植）
# ============================
def analyze_portfolio(df_portfolio, df_summary, df_strategy, df_realized):

    # --- realized_trades profit 計算 ---
    df_realized["profit"] = (df_realized["sell_price"] - df_realized["cost"]) * df_realized["shares"]
    realized_profit = df_realized["profit"].sum()
    df_summary.loc[df_summary["item"] == "利益確定済", "value"] = realized_profit

    # --- 株価取得 ---
    current_prices, values, profits, profit_rates = [], [], [], []

    for ticker, cost, shares in zip(df_portfolio["ticker"], df_portfolio["cost"], df_portfolio["shares"]):
        try:
            price = yf.Ticker(str(ticker)).history(period="1d")["Close"].iloc[-1]
        except Exception:
            price = None

        current_prices.append(price)

        if price is not None:
            value = price * shares
            profit = value - (cost * shares)
            profit_rate = profit / (cost * shares)
        else:
            value = None
            profit = None
            profit_rate = None

        values.append(value)
        profits.append(profit)
        profit_rates.append(profit_rate)

    df_portfolio["current_price"] = current_prices
    df_portfolio["value"] = values
    df_portfolio["profit"] = profits
    df_portfolio["profit_rate"] = profit_rates

    # summary 更新
    invested_amount = df_summary.loc[df_summary["item"] == "invested_amount", "value"].iloc[0]
    total_investment_frame = df_summary.loc[df_summary["item"] == "total_investment_frame", "value"].iloc[0]
    annual_target_profit = df_summary.loc[df_summary["item"] == "annual_target_profit", "value"].iloc[0]

    portfolio_value = df_portfolio["value"].sum()
    total_profit = (portfolio_value - invested_amount) + realized_profit
    total_profit_rate = total_profit / invested_amount
    remaining_cash = total_investment_frame - invested_amount + realized_profit
    progress_to_target = total_profit / annual_target_profit

    df_summary.loc[df_summary["item"] == "portfolio_value", "value"] = portfolio_value
    df_summary.loc[df_summary["item"] == "total_profit", "value"] = total_profit
    df_summary.loc[df_summary["item"] == "remaining_cash", "value"] = remaining_cash

    df_summary.loc[df_summary["item"] == "total_profit_rate", "value"] = f"{total_profit_rate * 100:.1f}%"
    df_summary.loc[df_summary["item"] == "progress_to_target", "value"] = f"{progress_to_target * 100:.1f}%"

    # ファンダメンタル
    fundamentals_list = [get_fundamentals(str(t)) for t in df_portfolio["ticker"]]
    fundamentals_df = pd.DataFrame(fundamentals_list)

    # 市場データ
    market_df = get_market_snapshot()

    # --- AI 戦略レポート（Azure OpenAI 版） ---
    prompt = f"""
あなたはプロの投資アナリストです。

【portfolio】
{df_portfolio.to_string()}

【summary】
{df_summary.to_string()}

【fundamentals】
{fundamentals_df.to_string()}

【market_snapshot】
{market_df.to_string()}

出力形式：

overall_view:
risk_assessment:
rebalance_plan:
target_achievement:
"""

    ai_res = client.chat.completions.create(
        model=AZURE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    strategy_text = ai_res.choices[0].message.content

    return df_portfolio, df_summary, strategy_text

# ============================
# API: Excel アップロード → 分析
# ============================
@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    xls = pd.ExcelFile(file.file)

    df_portfolio = pd.read_excel(xls, "portfolio")
    df_summary = pd.read_excel(xls, "summary")
    df_strategy = pd.read_excel(xls, "strategy")
    df_realized = pd.read_excel(xls, "realized_trades")

    portfolio, summary, strategy = analyze_portfolio(
        df_portfolio, df_summary, df_strategy, df_realized
    )

    return {
        "portfolio": portfolio.to_dict(orient="records"),
        "summary": summary.to_dict(orient="records"),
        "strategy": strategy
    }

# ============================
# スマホUI（HTML）
# ============================
@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Analyzer</title>
<style>
body { font-family: sans-serif; padding: 20px; }
button { padding: 10px 20px; font-size: 16px; }
pre { background: #f0f0f0; padding: 10px; white-space: pre-wrap; }
</style>
</head>
<body>

<h2>Excel アップロード</h2>
<input type="file" id="fileInput">
<button onclick="upload()">分析する</button>

<h3>結果</h3>
<pre id="result"></pre>

<script>
async function upload() {
    const file = document.getElementById("fileInput").files[0];
    if (!file) {
        alert("Excel ファイルを選択してください");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/analyze", {
        method: "POST",
        body: formData
    });

    const data = await res.json();
    document.getElementById("result").textContent =
        JSON.stringify(data, null, 2);
}
</script>

</body>
</html>
"""

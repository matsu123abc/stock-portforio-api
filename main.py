from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import io
import yfinance as yf
import os
import json

app = FastAPI()

# -----------------------------
# JSON 保存用ディレクトリ
# -----------------------------
DATA_DIR = "/home/data"
os.makedirs(DATA_DIR, exist_ok=True)

PORTFOLIO_JSON = os.path.join(DATA_DIR, "portfolio.json")
SUMMARY_JSON = os.path.join(DATA_DIR, "summary.json")


# -----------------------------
# JSON 保存
# -----------------------------
def save_json(portfolio, summary):
    with open(PORTFOLIO_JSON, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)

    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


# -----------------------------
# JSON 読み込み
# -----------------------------
def load_json():
    if not os.path.exists(PORTFOLIO_JSON):
        return None, None

    with open(PORTFOLIO_JSON, "r", encoding="utf-8") as f:
        portfolio = json.load(f)

    with open(SUMMARY_JSON, "r", encoding="utf-8") as f:
        summary = json.load(f)

    return portfolio, summary


# -----------------------------
# Excel アップロード → 計算 → JSON 保存
# -----------------------------
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        # Excel を BytesIO に変換
        contents = await file.read()
        excel_bytes = io.BytesIO(contents)

        # Excel 読み込み
        xls = pd.ExcelFile(excel_bytes)

        if "portfolio" not in xls.sheet_names:
            return JSONResponse(
                status_code=400,
                content={"error": "portfolio シートが見つかりません"}
            )

        # ---- portfolio 読み込み ----
        df = pd.read_excel(xls, sheet_name="portfolio")

        # 株価取得と計算
        current_prices = []
        values = []
        profits = []
        profit_rates = []

        for _, row in df.iterrows():
            ticker = str(row["ticker"])

            # yfinance で現在株価を取得
            try:
                price = yf.Ticker(ticker).history(period="1d")["Close"].iloc[-1]
            except:
                price = None

            current_prices.append(price)

            # 評価額
            if price is not None:
                value = price * row["shares"]
            else:
                value = None
            values.append(value)

            # 損益
            if value is not None:
                profit = value - (row["cost"] * row["shares"])
            else:
                profit = None
            profits.append(profit)

            # 損益率
            if profit is not None:
                profit_rate = profit / (row["cost"] * row["shares"])
            else:
                profit_rate = None
            profit_rates.append(profit_rate)

        # DataFrame に追加
        df["current_price"] = current_prices
        df["value"] = values
        df["profit"] = profits
        df["profit_rate"] = profit_rates

        # ---- summary 計算 ----
        if "summary" in xls.sheet_names:
            df_summary = pd.read_excel(xls, sheet_name="summary")

            total_investment_frame = int(df_summary.loc[df_summary["item"] == "total_investment_frame", "value"].values[0])
            annual_target_profit = int(df_summary.loc[df_summary["item"] == "annual_target_profit", "value"].values[0])
        else:
            total_investment_frame = 10000000
            annual_target_profit = 3000000

        # portfolio の集計（numpy → Python 型へ変換）
        invested_amount = int((df["cost"] * df["shares"]).sum())
        portfolio_value = float(df["value"].replace("", 0).sum())
        total_profit = float(portfolio_value - invested_amount)
        total_profit_rate = float(total_profit / invested_amount) if invested_amount > 0 else 0.0
        remaining_cash = int(total_investment_frame - invested_amount)
        progress_to_target = float(total_profit / annual_target_profit) if annual_target_profit > 0 else 0.0

        summary_json = {
            "total_investment_frame": int(total_investment_frame),
            "invested_amount": invested_amount,
            "portfolio_value": portfolio_value,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "remaining_cash": remaining_cash,
            "annual_target_profit": int(annual_target_profit),
            "progress_to_target": progress_to_target
        }

        # ---- NaN を空文字に変換 ----
        df = df.fillna("")

        # ---- buy_date を文字列に変換（重要）----
        if "buy_date" in df.columns:
            df["buy_date"] = df["buy_date"].astype(str)

        # JSON に変換
        portfolio_json = df.to_dict(orient="records")

        # ---- JSON 保存 ----
        save_json(portfolio_json, summary_json)

        # ---- 最終レスポンス ----
        return {
            "filename": file.filename,
            "portfolio_rows": len(portfolio_json),
            "portfolio": portfolio_json,
            "summary": summary_json,
            "message": "portfolio + summary calculated & saved"
        }

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Excel 読み込みエラー: {str(e)}"}
        )


# -----------------------------
# スマホ UI 用：保存された JSON を返す
# -----------------------------
@app.get("/data/get")
async def get_data():
    portfolio, summary = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    return {
        "portfolio": portfolio,
        "summary": summary
    }

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <html>
        <body>
            <h2>Excel アップロード</h2>
            <form action="/upload" enctype="multipart/form-data" method="post">
                <input name="file" type="file" />
                <button type="submit">アップロード</button>
            </form>
        </body>
    </html>
    """

@app.get("/mobile", response_class=HTMLResponse)
async def mobile():
    return """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: sans-serif; padding: 10px; }
            .card {
                border: 1px solid #ccc;
                padding: 10px;
                margin-bottom: 10px;
                border-radius: 8px;
            }
            .title { font-size: 18px; font-weight: bold; }
            .profit-positive { color: green; }
            .profit-negative { color: red; }
            button {
                padding: 6px 12px;
                margin-right: 5px;
                border-radius: 6px;
                border: none;
                background: #007bff;
                color: white;
            }
            .add-btn {
                background: #28a745;
                width: 100%;
                margin-top: 20px;
            }
        </style>
    </head>
    <body>

        <h2>📈 ポートフォリオ一覧</h2>
        <div id="list">読み込み中...</div>

        <button class="add-btn" onclick="alert('追加画面は Step9 で実装します')">
            ＋ 銘柄を追加
        </button>

        <script>
            async function loadData() {
                const res = await fetch('/data/get');
                const data = await res.json();

                if (data.error) {
                    document.getElementById('list').innerHTML = data.error;
                    return;
                }

                let html = "";
                data.portfolio.forEach(item => {
                    const profitClass = item.profit >= 0 ? "profit-positive" : "profit-negative";
                    const profitText = item.profit.toLocaleString();

                    html += `
                        <div class="card">
                            <div class="title">[${item.ticker}] ${item.name}</div>
                            <div>購入単価: ${item.cost} / 株数: ${item.shares}</div>
                            <div>購入日: ${item.buy_date}</div>
                            <div class="${profitClass}">
                                損益: ${profitText} 円
                            </div>
                            <button onclick="alert('編集は Step9 で実装します')">編集</button>
                            <button onclick="alert('削除は Step9 で実装します')">削除</button>
                        </div>
                    `;
                });

                document.getElementById('list').innerHTML = html;
            }

            loadData();
        </script>

    </body>
    </html>
    """

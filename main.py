from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import io
import yfinance as yf

app = FastAPI()

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

        # JSON に変換
        portfolio_json = df.to_dict(orient="records")

        # ---- 最終レスポンス ----
        return {
            "filename": file.filename,
            "portfolio_rows": len(portfolio_json),
            "portfolio": portfolio_json,
            "summary": summary_json,
            "message": "portfolio + summary calculated"
        }

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Excel 読み込みエラー: {str(e)}"}
        )


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Step4 Portfolio Price Test</title>
<style>
body { font-family: sans-serif; padding: 20px; }
button { padding: 10px 20px; font-size: 16px; }
pre { background: #f0f0f0; padding: 10px; white-space: pre-wrap; }
</style>
</head>
<body>

<h2>Step4: 株価取得 → 評価額 → 損益計算</h2>
<input type="file" id="fileInput">
<button onclick="upload()">アップロード</button>

<h3>結果</h3>
<pre id="result"></pre>

<script>
async function upload() {
    const file = document.getElementById("fileInput").files[0];
    if (!file) {
        alert("ファイルを選択してください");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/upload", {
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

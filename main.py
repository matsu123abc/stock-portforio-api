from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import io
import yfinance as yf
import os
import json
import requests
from bs4 import BeautifulSoup
from openai import AzureOpenAI

app = FastAPI()

# ============================
# Azure OpenAI クライアント
# ============================
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# ============================
# SerpAPI（Google News）
# ============================
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

def fetch_news_for_ticker(ticker, name):
    """
    Google News（SerpAPI）でニュースを取得する
    """
    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": f"{name} {ticker} ニュース",
        "api_key": SERPER_API_KEY,
        "num": 5
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
    except:
        return ["ニュース取得エラー"]

    articles = []

    def safe(v):
        return v if v else ""

    # top_stories
    if "top_stories" in data:
        for item in data["top_stories"]:
            articles.append(safe(item.get("title")))

    # organic_results
    if "organic_results" in data:
        for item in data["organic_results"]:
            articles.append(safe(item.get("title")))

    # news_results
    if "news_results" in data:
        for item in data["news_results"]:
            articles.append(safe(item.get("title")))

    return articles[:3] if articles else ["ニュースが見つかりませんでした。"]


# -----------------------------
# JSON 保存用ディレクトリ
# -----------------------------
DATA_DIR = "/home/data"
os.makedirs(DATA_DIR, exist_ok=True)

PORTFOLIO_JSON = os.path.join(DATA_DIR, "portfolio.json")
SUMMARY_JSON = os.path.join(DATA_DIR, "summary.json")
REALIZED_JSON = os.path.join(DATA_DIR, "realized_trades.json")


# -----------------------------
# JSON 保存
# -----------------------------
def save_json(portfolio, summary, realized_trades):
    with open(PORTFOLIO_JSON, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)

    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(REALIZED_JSON, "w", encoding="utf-8") as f:
        json.dump(realized_trades, f, ensure_ascii=False, indent=2)


# -----------------------------
# JSON 読み込み
# -----------------------------
def load_json():
    if not os.path.exists(PORTFOLIO_JSON) or not os.path.exists(SUMMARY_JSON):
        return None, None, None

    with open(PORTFOLIO_JSON, "r", encoding="utf-8") as f:
        portfolio = json.load(f)

    with open(SUMMARY_JSON, "r", encoding="utf-8") as f:
        summary = json.load(f)

    if os.path.exists(REALIZED_JSON):
        with open(REALIZED_JSON, "r", encoding="utf-8") as f:
            realized_trades = json.load(f)
    else:
        realized_trades = []

    if "ai_summary_comment" not in summary:
        summary["ai_summary_comment"] = ""

    return portfolio, summary, realized_trades


# -----------------------------
# AI コメント生成（SerpAPI ニュース版）
# -----------------------------
def generate_ai_comment(item):
    ticker = item["ticker"]

    # --- 業績データ取得 ---
    yf_ticker = yf.Ticker(ticker)
    info = yf_ticker.info

    company_summary = info.get("longBusinessSummary", "")
    sector = info.get("sector", "")
    industry = info.get("industry", "")
    market_cap = info.get("marketCap", "")
    revenue = info.get("totalRevenue", "")
    profit_margin = info.get("profitMargins", "")
    pe_ratio = info.get("trailingPE", "")
    eps = info.get("trailingEps", "")

    # --- ニュース取得（SerpAPI） ---
    news_list = fetch_news_for_ticker(item["ticker"], item["name"])
    news_text = "\n".join(news_list)

    # --- AI プロンプト ---
    prompt = f"""
あなたはプロの株式アナリストです。
以下の銘柄について、業績・ニュース・株価を総合的に分析し、
投資家にとって価値のあるコメントを作成してください。

【銘柄情報】
ティッカー: {item['ticker']}
銘柄名: {item['name']}
購入単価: {item['cost']}
株数: {item['shares']}
現在値: {item['current_price']}
損益: {item['profit']}
損益率: {item['profit_rate']}

【業績データ】
セクター: {sector}
業種: {industry}
時価総額: {market_cap}
売上高: {revenue}
利益率: {profit_margin}
PER: {pe_ratio}
EPS: {eps}

【会社概要】
{company_summary}

【最新ニュース（Google News）】
{news_text}

【出力形式】
### 現状の評価
（業績・ニュースを踏まえた評価）

### 今後の戦略
（買い増し / ホールド / 利益確定）

### 注意点
（業績リスク・競合リスク・市場リスク）
"""

    res = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1500
    )

    return res.choices[0].message.content.strip()


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

        # ---- 実現利益（realized_trades） ----
        realized_profit_total = 0
        for t in realized_trades_json:
            realized_profit_total += (t["sell_price"] - t["cost"]) * t["shares"]

        # ---- Summary を売却履歴込みに修正 ----
        summary_json["realized_profit"] = realized_profit_total
        summary_json["unrealized_profit"] = total_profit
        summary_json["total_profit"] = total_profit + realized_profit_total
        summary_json["total_profit_rate"] = summary_json["total_profit"] / invested_amount
        summary_json["progress_to_target"] = summary_json["total_profit"] / annual_target_profit

        # ---- realized_trades 読み込み ----
        if "realized_trades" in xls.sheet_names:
            df_trades = pd.read_excel(xls, sheet_name="realized_trades")
            df_trades = df_trades.fillna("")
            if "sell_date" in df_trades.columns:
                df_trades["sell_date"] = df_trades["sell_date"].astype(str)
            realized_trades_json = df_trades.to_dict(orient="records")
        else:
            realized_trades_json = []

        # ---- NaN を空文字に変換 ----
        df = df.fillna("")

        # ---- buy_date を文字列に変換（重要）----
        if "buy_date" in df.columns:
            df["buy_date"] = df["buy_date"].astype(str)

        # JSON に変換
        portfolio_json = df.to_dict(orient="records")

        # ---- JSON 保存 ----
        save_json(portfolio_json, summary_json, realized_trades_json)

        # ---- 最終レスポンス ----
        return {
            "filename": file.filename,
            "portfolio_rows": len(portfolio_json),
            "portfolio": portfolio_json,
            "summary": summary_json,
            "realized_trades": realized_trades_json,
            "message": "portfolio + summary + realized_trades calculated & saved"
        }

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Excel 読み込みエラー: {str(e)}"}
        )


# -----------------------------
# update_prices
# -----------------------------
@app.post("/update_prices")
async def update_prices():
    portfolio, summary, realized_trades = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    # DataFrame に戻す
    df = pd.DataFrame(portfolio)

    current_prices = []
    values = []
    profits = []
    profit_rates = []

    for idx, row in df.iterrows():
        ticker = str(row["ticker"])

        # --- 株価取得（1日 → 5日 fallback） ---
        price = None
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if len(hist) > 0:
                price = hist["Close"].iloc[-1]
        except:
            price = None

        # 1日データが取れない場合は5日データ
        if price is None:
            try:
                hist = yf.Ticker(ticker).history(period="5d")
                if len(hist) > 0:
                    price = hist["Close"].iloc[-1]
            except:
                price = None

        # --- ★ 前回値を使う（最重要） ---
        if price is None:
            price = row.get("current_price", None)

        current_prices.append(price)

        # --- 評価額 ---
        if price is not None:
            value = price * row["shares"]
        else:
            value = None
        values.append(value)

        # --- 損益 ---
        if value is not None:
            profit = value - (row["cost"] * row["shares"])
        else:
            profit = None
        profits.append(profit)

        # --- 損益率 ---
        if profit is not None:
            profit_rate = profit / (row["cost"] * row["shares"])
        else:
            profit_rate = None
        profit_rates.append(profit_rate)

    # DataFrame に反映
    df["current_price"] = current_prices
    df["value"] = values
    df["profit"] = profits
    df["profit_rate"] = profit_rates

    # --- summary 再計算 ---
    invested_amount = int((df["cost"] * df["shares"]).sum())
    portfolio_value = float(df["value"].replace("", 0).sum())
    total_profit = float(portfolio_value - invested_amount)
    total_profit_rate = float(total_profit / invested_amount) if invested_amount > 0 else 0.0

    total_investment_frame = summary["total_investment_frame"]
    annual_target_profit = summary["annual_target_profit"]

    remaining_cash = int(total_investment_frame - invested_amount)
    progress_to_target = float(total_profit / annual_target_profit)

    summary_new = {
        "total_investment_frame": total_investment_frame,
        "invested_amount": invested_amount,
        "portfolio_value": portfolio_value,
        "total_profit": total_profit,
        "total_profit_rate": total_profit_rate,
        "remaining_cash": remaining_cash,
        "annual_target_profit": annual_target_profit,
        "progress_to_target": progress_to_target
    }

    # buy_date を文字列化
    if "buy_date" in df.columns:
        df["buy_date"] = df["buy_date"].astype(str)

    portfolio_new = df.fillna("").to_dict(orient="records")

    # JSON 保存（realized_trades はそのまま維持）
    save_json(portfolio_new, summary_new, realized_trades)

    return {
        "message": "株価を更新しました",
        "portfolio": portfolio_new,
        "summary": summary_new
    }


# -----------------------------
# AI コメント更新（generate_ai_comment を使用）
# -----------------------------
@app.post("/update_ai_comment")
async def update_ai_comment():
    portfolio, summary, realized_trades = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    updated_portfolio = []

    for item in portfolio:
        try:
            item["ai_comment"] = generate_ai_comment(item)
        except Exception as e:
            item["ai_comment"] = f"AI コメント生成エラー: {str(e)}"

        updated_portfolio.append(item)

    save_json(updated_portfolio, summary, realized_trades)

    return {
        "message": "AI コメントを更新しました",
        "portfolio": updated_portfolio
    }


# -----------------------------
# update_ai_summary
# -----------------------------
@app.post("/update_ai_summary")
async def update_ai_summary():
    portfolio, summary, realized_trades = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    prompt = f"""
あなたはプロの投資アナリストです。
以下のポートフォリオ全体を分析し、総合的な戦略コメントを作成してください。

【ポートフォリオ概要】
投資額: {summary['invested_amount']:,} 円
評価額: {summary['portfolio_value']:,} 円
損益: {summary['total_profit']:,} 円
損益率: {summary['total_profit_rate']*100:.2f} %
残りキャッシュ: {summary['remaining_cash']:,} 円
目標達成率: {summary['progress_to_target']*100:.2f} %

【銘柄一覧】
{json.dumps(portfolio, ensure_ascii=False, indent=2)}

【出力形式】
### 総合評価
（全体の状況を簡潔に）

### 今後の戦略
（買い増し・利益確定・リバランスなど）

### 注意点
（市場リスク、セクターリスクなど）
"""

    try:
        res = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500
        )
        ai_summary = res.choices[0].message.content.strip()

    except Exception as e:
        ai_summary = f"AI 統括コメント生成エラー: {str(e)}"

    summary["ai_summary_comment"] = ai_summary

    save_json(portfolio, summary, realized_trades)

    return {
        "message": "AI 統括コメントを更新しました",
        "summary": summary
    }


# -----------------------------
# スマホ UI 用：保存された JSON を返す
# -----------------------------
@app.get("/data/get")
async def get_data():
    portfolio, summary, realized_trades = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    return {
        "portfolio": portfolio,
        "summary": summary,
        "realized_trades": realized_trades
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
            .summary-box {
                background: #f5f5f5;
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 20px;
            }
            .summary-title {
                font-size: 20px;
                font-weight: bold;
                margin-bottom: 10px;
            }
        </style>
    </head>
    <body>

        <h2>📈 ポートフォリオ一覧</h2>

        <button onclick="updatePrices()" style="background:#ff9800; width:100%; margin-bottom:20px;">
        🔄 株価を更新する
        </button>

        <button onclick="updateAI()" style="background:#673ab7; width:100%; margin-bottom:20px;">
            🤖 AI コメントを更新する
        </button>

        <button onclick="updateAISummary()" style="background:#3f51b5; width:100%; margin-bottom:20px;">
            📘 AI 統括コメントを更新する
        </button>

        <!-- Summary 表示エリア -->
        <div class="summary-box" id="summary">
            Summary を読み込み中...
        </div>

        <div class="summary-box" id="ai_summary">
            AI統括コメントを読み込み中...
        </div>
                
        <!-- 銘柄一覧 -->
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
                    document.getElementById('summary').innerHTML = "";
                    return;
                }

                // ---- Summary 表示 ----
                const s = data.summary;

                document.getElementById('summary').innerHTML = `
                    <div class="summary-title">📊 Summary</div>
                    <div>投資枠: ${s.total_investment_frame.toLocaleString()} 円</div>
                    <div>投資額: ${s.invested_amount.toLocaleString()} 円</div>
                    <div>評価額: ${s.portfolio_value.toLocaleString()} 円</div>

                    <div>実現利益: ${s.realized_profit.toLocaleString()} 円</div>
                    <div>含み損益: ${s.unrealized_profit.toLocaleString()} 円</div>
                    <div>総合損益: ${s.total_profit.toLocaleString()} 円</div>

                    <div>損益率: ${(s.total_profit_rate * 100).toFixed(2)} %</div>
                    <div>残りキャッシュ: ${s.remaining_cash.toLocaleString()} 円</div>
                    <div>目標達成率: ${(s.progress_to_target * 100).toFixed(2)} %</div>
                `;

                // ---- 保有株式一覧 ----
                let tableHtml = `
                    <div class="summary-title">📋 保有株式一覧</div>
                    <table style="width:100%; border-collapse: collapse; font-size:16px;">
                        <tr style="background:#e0e0e0;">
                            <th style="padding:6px; border:1px solid #ccc;">銘柄名</th>
                            <th style="padding:6px; border:1px solid #ccc;">現在値</th>
                            <th style="padding:6px; border:1px solid #ccc;">購入単価</th>
                            <th style="padding:6px; border:1px solid #ccc;">損益</th>
                        </tr>
                `;

                data.portfolio.forEach(item => {
                    const profitClass = item.profit >= 0 ? "color:green;" : "color:red;";
                    tableHtml += `
                        <tr>
                            <td style="padding:6px; border:1px solid #ccc;">${item.name}</td>
                            <td style="padding:6px; border:1px solid #ccc;">${item.current_price}</td>
                            <td style="padding:6px; border:1px solid #ccc;">${item.cost}</td>
                            <td style="padding:6px; border:1px solid #ccc; ${profitClass}">
                                ${item.profit.toLocaleString()}
                            </td>
                        </tr>
                    `;
                });

                tableHtml += "</table>";

                // Summary の下に挿入
                document.getElementById('summary').insertAdjacentHTML("afterend", tableHtml);


                // ============================================================
                // 📘 売却履歴（realized_trades） ← ★ここを追加
                // ============================================================
                if (data.realized_trades && data.realized_trades.length > 0) {

                    let tradesHtml = `
                        <div class="summary-title">📘 売却履歴</div>
                        <table style="width:100%; border-collapse: collapse; font-size:16px;">
                            <tr style="background:#e0e0e0;">
                                <th style="padding:6px; border:1px solid #ccc;">銘柄名</th>
                                <th style="padding:6px; border:1px solid #ccc;">売却日</th>
                                <th style="padding:6px; border:1px solid #ccc;">株数</th>
                                <th style="padding:6px; border:1px solid #ccc;">売却価格</th>
                                <th style="padding:6px; border:1px solid #ccc;">取得単価</th>
                                <th style="padding:6px; border:1px solid #ccc;">実現利益</th>
                            </tr>
                    `;

                    data.realized_trades.forEach(t => {
                        const realizedProfit = (t.sell_price - t.cost) * t.shares;
                        const profitColor = realizedProfit >= 0 ? "color:green;" : "color:red;";

                        tradesHtml += `
                            <tr>
                                <td style="padding:6px; border:1px solid #ccc;">${t.name}</td>
                                <td style="padding:6px; border:1px solid #ccc;">${t.sell_date}</td>
                                <td style="padding:6px; border:1px solid #ccc;">${t.shares}</td>
                                <td style="padding:6px; border:1px solid #ccc;">${t.sell_price}</td>
                                <td style="padding:6px; border:1px solid #ccc;">${t.cost}</td>
                                <td style="padding:6px; border:1px solid #ccc; ${profitColor}">
                                    ${realizedProfit.toLocaleString()}
                                </td>
                            </tr>
                        `;
                    });

                    tradesHtml += "</table>";

                    // 保有株式一覧の下に売却履歴を追加
                    document.getElementById('summary').insertAdjacentHTML("afterend", tradesHtml);
                }


                // ---- 銘柄カード一覧 ----
                let html = "";
                data.portfolio.forEach(item => {
                    const profitClass = item.profit >= 0 ? "profit-positive" : "profit-negative";
                    const profitText = item.profit.toLocaleString();

                    html += `
                        <div class="card">
                            <div class="title">[${item.ticker}] ${item.name}</div>
                            <div>購入単価: ${item.cost} / 株数: ${item.shares}</div>
                            <div>購入日: ${item.buy_date}</div>
                            <div>現在値: ${item.current_price}</div>

                            <div class="${profitClass}">
                                損益: ${profitText} 円
                            </div>

                            <div style="
                                margin-top:10px;
                                padding:10px;
                                background:#eef;
                                border-radius:6px;
                                white-space:pre-wrap;
                            ">
                                <b>AI コメント</b><br>
                                <div style="font-size:14px; line-height:1.5;">
                                    ${item.ai_comment || "（コメントなし）"}
                                </div>
                            </div>

                            <button onclick="alert('編集は Step9 で実装します')">編集</button>
                            <button onclick="alert('削除は Step9 で実装します')">削除</button>
                        </div>
                    `;
                });

                document.getElementById('ai_summary').innerHTML = `
                    <div class="summary-title">🤖 AI 統括コメント</div>
                    <div style="white-space:pre-wrap; line-height:1.5;">
                        ${data.summary.ai_summary_comment || "（まだ生成されていません）"}
                    </div>
                `;

                document.getElementById('list').innerHTML = html;
            }

            loadData();

            async function updatePrices() {
                const res = await fetch('/update_prices', { method: 'POST' });
                const data = await res.json();
                if (data.error) { alert(data.error); return; }
                alert("株価を更新しました！");
                loadData();
            }

            async function updateAI() {
                const res = await fetch('/update_ai_comment', { method: 'POST' });
                const data = await res.json();
                if (data.error) { alert(data.error); return; }
                alert("AI コメントを更新しました！");
                loadData();
            }

            async function updateAISummary() {
                const res = await fetch('/update_ai_summary', { method: 'POST' });
                const data = await res.json();
                if (data.error) { alert(data.error); return; }
                alert("AI 統括コメントを更新しました！");
                loadData();
            }

        </script>

    </body>
    </html>
    """


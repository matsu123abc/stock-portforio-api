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
    # 一時ファイルに書いてからリネームすることで途中書き込みを防ぐ（簡易的な原子書き込み）
    def _atomic_write(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    _atomic_write(PORTFOLIO_JSON, portfolio)
    _atomic_write(SUMMARY_JSON, summary)
    _atomic_write(REALIZED_JSON, realized_trades)

# -----------------------------
# JSON 読み込み
# -----------------------------
def load_json():
    # ファイルが無ければ None/空を返す
    if not os.path.exists(PORTFOLIO_JSON) or not os.path.exists(SUMMARY_JSON):
        return None, None, []

    # portfolio
    try:
        with open(PORTFOLIO_JSON, "r", encoding="utf-8") as f:
            portfolio = json.load(f)
    except Exception:
        portfolio = None

    # summary
    try:
        with open(SUMMARY_JSON, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except Exception:
        summary = {}

    # realized_trades
    try:
        if os.path.exists(REALIZED_JSON):
            with open(REALIZED_JSON, "r", encoding="utf-8") as f:
                realized_trades = json.load(f)
        else:
            realized_trades = []
    except Exception:
        realized_trades = []

    # summary の必須キーを補完（UI 停止を防ぐ）
    summary.setdefault("ai_summary_comment", "")
    summary.setdefault("realized_profit", 0)
    summary.setdefault("unrealized_profit", 0)
    summary.setdefault("total_profit", summary.get("total_profit", 0))
    summary.setdefault("total_profit_rate", summary.get("total_profit_rate", 0.0))
    summary.setdefault("progress_to_target", summary.get("progress_to_target", 0.0))
    summary.setdefault("total_investment_frame", summary.get("total_investment_frame", 10000000))
    summary.setdefault("invested_amount", summary.get("invested_amount", 0))
    summary.setdefault("portfolio_value", summary.get("portfolio_value", 0))
    summary.setdefault("annual_target_profit", summary.get("annual_target_profit", 3000000))
    summary.setdefault("remaining_cash", summary.get("remaining_cash", summary.get("total_investment_frame", 10000000) - summary.get("invested_amount", 0)))

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

        # ---- realized_trades 読み込み（先に読み込んでおく） ----
        if "realized_trades" in xls.sheet_names:
            df_trades = pd.read_excel(xls, sheet_name="realized_trades")
            df_trades = df_trades.fillna("")
            if "sell_date" in df_trades.columns:
                df_trades["sell_date"] = df_trades["sell_date"].astype(str)
            realized_trades_json = df_trades.to_dict(orient="records")
        else:
            realized_trades_json = []

        # NaN を空文字に変換（計算前に数値列はそのままにしておく）
        df = df.fillna("")

        # ---- buy_date を文字列に変換（重要）----
        if "buy_date" in df.columns:
            df["buy_date"] = df["buy_date"].astype(str)

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
            try:
                shares = float(row["shares"])
            except:
                shares = 0.0

            if price is not None:
                value = price * shares
            else:
                value = None
            values.append(value)

            # 損益
            try:
                cost = float(row["cost"])
            except:
                cost = 0.0

            if value is not None:
                profit = value - (cost * shares)
            else:
                profit = None
            profits.append(profit)

            # 損益率
            if profit is not None and (cost * shares) != 0:
                profit_rate = profit / (cost * shares)
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
        # df["cost"] と df["shares"] が文字列になっている可能性があるため安全に変換
        try:
            invested_amount = int((df["cost"].astype(float) * df["shares"].astype(float)).sum())
        except:
            # フォールバック：ゼロや欠損を無視して計算
            invested_amount = int(sum([ (float(r.get("cost") or 0) * float(r.get("shares") or 0)) for r in df.to_dict(orient="records") ]))

        # portfolio_value は value 列の合計（None を 0 として扱う）
        portfolio_value = float(sum([ (v if (v is not None and v != "") else 0) for v in df["value"].tolist() ]))
        total_profit = float(portfolio_value - invested_amount)
        total_profit_rate = float(total_profit / invested_amount) if invested_amount > 0 else 0.0
        remaining_cash = int(total_investment_frame - invested_amount)
        progress_to_target = float(total_profit / annual_target_profit) if annual_target_profit > 0 else 0.0

        summary_json = {
            "total_investment_frame": int(total_investment_frame),
            "invested_amount": invested_amount,
            "portfolio_value": portfolio_value,
            "total_profit": total_profit,               # ここは「含み損益（保有分）」として扱う
            "total_profit_rate": total_profit_rate,
            "remaining_cash": remaining_cash,
            "annual_target_profit": int(annual_target_profit),
            "progress_to_target": progress_to_target
        }

        # ---- 実現利益（realized_trades_json が既に作られている前提） ----
        realized_profit_total = 0
        for t in realized_trades_json:
            try:
                sell_price = float(t.get("sell_price") or 0)
            except:
                sell_price = 0.0
            try:
                cost = float(t.get("cost") or 0)
            except:
                cost = 0.0
            try:
                shares = float(t.get("shares") or 0)
            except:
                shares = 0.0
            realized_profit_total += (sell_price - cost) * shares

        # ---- Summary を売却履歴込みに修正 ----
        summary_json["realized_profit"] = int(realized_profit_total)
        summary_json["unrealized_profit"] = int(total_profit)
        summary_json["total_profit"] = int(total_profit + realized_profit_total)
        summary_json["total_profit_rate"] = (summary_json["total_profit"] / invested_amount) if invested_amount > 0 else 0.0
        summary_json["progress_to_target"] = (summary_json["total_profit"] / annual_target_profit) if annual_target_profit > 0 else 0.0

        # JSON に変換（portfolio は既に NaN を "" に変換済み）
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

    # --- realized_trades を集計（銘柄ごとの売却合計株数） ---
    sold_shares_by_ticker = {}
    for t in (realized_trades or []):
        tk = str(t.get("ticker") or "").strip()
        try:
            shares_sold = float(t.get("shares") or 0)
        except:
            shares_sold = 0.0
        sold_shares_by_ticker[tk] = sold_shares_by_ticker.get(tk, 0.0) + shares_sold

    # DataFrame に戻す
    df = pd.DataFrame(portfolio)

    # NaN を "" にしておく（既存の処理）
    df = df.fillna("")

    # buy_date を文字列化（もしあれば）
    if "buy_date" in df.columns:
        df["buy_date"] = df["buy_date"].astype(str)

    # --- 保有から売却分を差し引く（FIFO 風に行ごとに売却を割り当てる） ---
    # sold_shares_by_ticker の値を消費しながら各行の shares を減らす
    rows = []
    for idx, row in df.iterrows():
        tk = str(row.get("ticker") or "").strip()
        try:
            orig_shares = float(row.get("shares") or 0)
        except:
            orig_shares = 0.0

        sold_remaining = sold_shares_by_ticker.get(tk, 0.0)
        if sold_remaining <= 0:
            # 売却なし
            remaining = orig_shares
        else:
            # この行から差し引ける分を計算
            deduct = min(orig_shares, sold_remaining)
            remaining = orig_shares - deduct
            sold_shares_by_ticker[tk] = sold_remaining - deduct

        if remaining > 0:
            new_row = row.copy()
            # 保持する shares は残存数（整数にしたければ int(remaining) に変更）
            new_row["shares"] = remaining
            rows.append(new_row)
        else:
            # 完全売却ならこの行は除外
            pass

    # 再構築した DataFrame を使う
    if len(rows) == 0:
        # 保有がゼロの場合は空の DataFrame を作る（列は元の df の列を維持）
        df = pd.DataFrame(columns=df.columns)
    else:
        df = pd.DataFrame(rows)

    current_prices = []
    values = []
    profits = []
    profit_rates = []

    for idx, row in df.iterrows():
        ticker = str(row.get("ticker", ""))

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

        # --- 前回値を使う（最重要） ---
        if price is None:
            price = row.get("current_price", None)

        current_prices.append(price)

        # --- 評価額 ---
        try:
            shares = float(row.get("shares") or 0)
        except:
            shares = 0.0

        if price is not None:
            value = price * shares
        else:
            value = None
        values.append(value)

        # --- 損益 ---
        try:
            cost = float(row.get("cost") or 0)
        except:
            cost = 0.0

        if value is not None:
            profit = value - (cost * shares)
        else:
            profit = None
        profits.append(profit)

        # --- 損益率 ---
        if profit is not None and (cost * shares) != 0:
            profit_rate = profit / (cost * shares)
        else:
            profit_rate = None
        profit_rates.append(profit_rate)

    # DataFrame に反映
    df["current_price"] = current_prices
    df["value"] = values
    df["profit"] = profits
    df["profit_rate"] = profit_rates

    # --- summary 再計算（売却反映済みの df を使う） ---
    # invested_amount は cost * shares の合計（安全に数値変換）
    try:
        invested_amount = int((df["cost"].astype(float) * df["shares"].astype(float)).sum())
    except:
        invested_amount = int(sum([ (float(r.get("cost") or 0) * float(r.get("shares") or 0)) for r in df.to_dict(orient="records") ]))

    # portfolio_value は value 列の合計（None を 0 として扱う）
    portfolio_value = float(sum([ (v if (v is not None and v != "") else 0) for v in df["value"].tolist() ]))
    unrealized_profit = float(portfolio_value - invested_amount)
    unrealized_profit_rate = float(unrealized_profit / invested_amount) if invested_amount > 0 else 0.0

    total_investment_frame = summary.get("total_investment_frame", 0)
    annual_target_profit = summary.get("annual_target_profit", 0)

    remaining_cash = int(total_investment_frame - invested_amount) if total_investment_frame is not None else 0

    # ---- 実現利益（realized_trades を使って計算） ----
    realized_profit_total = 0
    for t in (realized_trades or []):
        try:
            sell_price = float(t.get("sell_price") or 0)
        except:
            sell_price = 0.0
        try:
            cost_r = float(t.get("cost") or 0)
        except:
            cost_r = 0.0
        try:
            shares_r = float(t.get("shares") or 0)
        except:
            shares_r = 0.0
        realized_profit_total += (sell_price - cost_r) * shares_r

    # 総合損益（実現 + 含み）
    total_profit_combined = unrealized_profit + realized_profit_total
    total_profit_rate_combined = (total_profit_combined / invested_amount) if invested_amount > 0 else 0.0
    progress_to_target = (total_profit_combined / annual_target_profit) if annual_target_profit and annual_target_profit > 0 else 0.0

    summary_new = {
        "total_investment_frame": total_investment_frame,
        "invested_amount": invested_amount,
        "portfolio_value": portfolio_value,
        # 含み損益（保有分）
        "unrealized_profit": unrealized_profit,
        # 実現損益（売却済み）
        "realized_profit": realized_profit_total,
        # 総合損益（実現 + 含み）
        "total_profit": total_profit_combined,
        "total_profit_rate": total_profit_rate_combined,
        "remaining_cash": remaining_cash,
        "annual_target_profit": annual_target_profit,
        "progress_to_target": progress_to_target
    }

    # buy_date を文字列化（再確認）
    if "buy_date" in df.columns:
        df["buy_date"] = df["buy_date"].astype(str)

    portfolio_new = df.fillna("").to_dict(orient="records")

    # JSON 保存（realized_trades はそのまま維持）
    save_json(portfolio_new, summary_new, realized_trades)

    return {
        "message": "株価を更新しました",
        "portfolio": portfolio_new,
        "summary": summary_new,
        "realized_trades": realized_trades
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

    if summary is None:
        return {"error": "まだデータが保存されていません"}

    # AI統括コメントは不要 → 空にする
    summary["ai_summary_comment"] = ""

    save_json(portfolio, summary, realized_trades)

    return {
        "message": "AI 統括コメントをクリアしました",
        "ai_summary_comment": ""
    }

# -----------------------------
# スマホ UI 用：保存された JSON を返す
# -----------------------------
@app.get("/data/get")
async def get_data():
    portfolio, summary, realized_trades = load_json()

    if portfolio is None:
        return {"error": "まだデータが保存されていません"}

    # safety: ensure lists/dicts
    if summary is None:
        summary = {}
    if realized_trades is None:
        realized_trades = []
    if portfolio is None:
        portfolio = []

    # --- realized_trades を集計（銘柄ごとの売却合計株数） ---
    sold_shares_by_ticker = {}
    for t in (realized_trades or []):
        tk = str(t.get("ticker") or "").strip()
        try:
            shares_sold = float(t.get("shares") or 0)
        except:
            shares_sold = 0.0
        sold_shares_by_ticker[tk] = sold_shares_by_ticker.get(tk, 0.0) + shares_sold

    # --- portfolio から売却分を差し引いて残存保有を作る（行ごとに売却を割り当てる） ---
    adjusted_rows = []
    for p in (portfolio or []):
        try:
            tk = str(p.get("ticker") or "").strip()
        except:
            tk = ""
        try:
            orig_shares = float(p.get("shares") or 0)
        except:
            orig_shares = 0.0

        sold_remaining = sold_shares_by_ticker.get(tk, 0.0)
        if sold_remaining <= 0:
            remaining = orig_shares
        else:
            deduct = min(orig_shares, sold_remaining)
            remaining = orig_shares - deduct
            sold_shares_by_ticker[tk] = sold_remaining - deduct

        if remaining > 0:
            new_p = dict(p)  # shallow copy
            # keep numeric type consistent
            # if original shares were int-like, you may want int(remaining)
            new_p["shares"] = remaining
            adjusted_rows.append(new_p)
        else:
            # 完全売却なら除外
            pass

    adjusted_portfolio = adjusted_rows

    # --- 実現利益が無ければ realized_trades から計算 ---
    realized_profit_total = 0
    for t in (realized_trades or []):
        try:
            sell_price = float(t.get("sell_price") or 0)
        except:
            sell_price = 0.0
        try:
            cost = float(t.get("cost") or 0)
        except:
            cost = 0.0
        try:
            shares = float(t.get("shares") or 0)
        except:
            shares = 0.0
        realized_profit_total += (sell_price - cost) * shares
    summary["realized_profit"] = int(realized_profit_total)

    # --- 含み損益（unrealized_profit）を adjusted_portfolio から計算 ---
    invested = 0.0
    value = 0.0
    for p in adjusted_portfolio:
        try:
            c = float(p.get("cost") or 0)
        except:
            c = 0.0
        try:
            s = float(p.get("shares") or 0)
        except:
            s = 0.0
        invested += c * s

        v = p.get("value", None)
        if v is None or v == "":
            try:
                cp = float(p.get("current_price") or 0)
            except:
                cp = 0.0
            value += cp * s
        else:
            try:
                value += float(v)
            except:
                # fallback to current_price if value parsing fails
                try:
                    cp = float(p.get("current_price") or 0)
                except:
                    cp = 0.0
                value += cp * s

    unrealized = value - invested
    summary["unrealized_profit"] = int(unrealized)

    # ensure invested_amount and portfolio_value exist in summary
    summary["invested_amount"] = int(invested)
    summary["portfolio_value"] = float(value)

    # --- 総合損益（total_profit） ---
    summary["total_profit"] = int(summary.get("realized_profit", 0) + summary.get("unrealized_profit", 0))

    # --- total_profit_rate / progress_to_target の補完（安全に） ---
    invested_amount = float(summary.get("invested_amount") or 0)
    try:
        summary["total_profit_rate"] = (summary["total_profit"] / invested_amount) if invested_amount > 0 else 0.0
    except Exception:
        summary["total_profit_rate"] = 0.0

    annual_target = float(summary.get("annual_target_profit") or 0)
    try:
        summary["progress_to_target"] = (summary["total_profit"] / annual_target) if annual_target > 0 else 0.0
    except Exception:
        summary["progress_to_target"] = 0.0

    # remaining_cash 補完
    total_investment_frame = float(summary.get("total_investment_frame") or 0)
    try:
        summary["remaining_cash"] = int(total_investment_frame - summary.get("invested_amount", 0))
    except Exception:
        summary["remaining_cash"] = int(total_investment_frame - invested)

    summary.setdefault("ai_summary_comment", "")

    return {
        "portfolio": adjusted_portfolio,
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
            table { width:100%; border-collapse: collapse; font-size:16px; }
            th, td { padding:6px; border:1px solid #ccc; text-align:left; }
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

        <!-- Summary 表示エリア -->
        <div class="summary-box" id="summary">
            Summary を読み込み中...
        </div>

        <!-- Summary の下に差し込む領域（保有一覧・売却履歴をここに上書き） -->
        <div id="after_summary"></div>
                
        <!-- 銘柄一覧 -->
        <div id="list">読み込み中...</div>

        <button class="add-btn" onclick="alert('追加画面は Step9 で実装します')">
            ＋ 銘柄を追加
        </button>

        <script>
            function safeNumber(v) {
                const n = Number(v);
                return Number.isFinite(n) ? n : 0;
            }

            function fmt(n) {
                return safeNumber(n).toLocaleString();
            }

            async function loadData() {
                try {
                    const res = await fetch('/data/get');
                    const data = await res.json();

                    if (data.error) {
                        document.getElementById('list').innerHTML = data.error;
                        document.getElementById('summary').innerHTML = "";
                        document.getElementById('after_summary').innerHTML = "";
                        document.getElementById('ai_summary').innerHTML = "";
                        return;
                    }

                    // ---- Summary 表示 ----
                    const s = data.summary || {};

                    // 安全に値を扱う（未定義なら 0 を使う）
                    const total_investment_frame = safeNumber(s.total_investment_frame);
                    const invested_amount = safeNumber(s.invested_amount);
                    const portfolio_value = safeNumber(s.portfolio_value);
                    const realized_profit = safeNumber(s.realized_profit);
                    const unrealized_profit = safeNumber(s.unrealized_profit);
                    const total_profit = safeNumber(s.total_profit);
                    const total_profit_rate = safeNumber(s.total_profit_rate);
                    const remaining_cash = safeNumber(s.remaining_cash);
                    const progress_to_target = safeNumber(s.progress_to_target);

                    document.getElementById('summary').innerHTML = `
                        <div class="summary-title">📊 Summary</div>
                        <div>投資枠: ${total_investment_frame.toLocaleString()} 円</div>
                        <div>投資額: ${invested_amount.toLocaleString()} 円</div>
                        <div>評価額: ${portfolio_value.toLocaleString()} 円</div>

                        <div>実現利益: ${realized_profit.toLocaleString()} 円</div>
                        <div>含み損益: ${unrealized_profit.toLocaleString()} 円</div>
                        <div>総合損益: ${total_profit.toLocaleString()} 円</div>

                        <div>損益率: ${(total_profit_rate * 100).toFixed(2)} %</div>
                        <div>残りキャッシュ: ${remaining_cash.toLocaleString()} 円</div>
                        <div>目標達成率: ${(progress_to_target * 100).toFixed(2)} %</div>
                    `;

                    // ---- 保有株式一覧 ----
                    let tableHtml = `
                        <div class="summary-title">📋 保有株式一覧</div>
                        <table>
                            <tr style="background:#e0e0e0;">
                                <th>銘柄名</th>
                                <th>現在値</th>
                                <th>購入単価</th>
                                <th>損益</th>
                            </tr>
                    `;

                    (data.portfolio || []).forEach(item => {
                        const profit = safeNumber(item.profit);
                        const profitStyle = profit >= 0 ? "color:green;" : "color:red;";
                        const currentPrice = item.current_price === null || item.current_price === "" ? "-" : item.current_price;
                        tableHtml += `
                            <tr>
                                <td>${item.name || ""}</td>
                                <td>${currentPrice}</td>
                                <td>${fmt(item.cost)}</td>
                                <td style="${profitStyle}">${fmt(profit)}</td>
                            </tr>
                        `;
                    });

                    tableHtml += "</table>";

                    // ============================================================
                    // 📘 売却履歴（realized_trades）
                    // ============================================================
                    let tradesHtml = "";
                    if (data.realized_trades && data.realized_trades.length > 0) {
                        tradesHtml = `
                            <div class="summary-title">📘 売却履歴</div>
                            <table>
                                <tr style="background:#e0e0e0;">
                                    <th>銘柄名</th>
                                    <th>売却日</th>
                                    <th>株数</th>
                                    <th>売却価格</th>
                                    <th>取得単価</th>
                                    <th>実現利益</th>
                                </tr>
                        `;

                        data.realized_trades.forEach(t => {
                            const sellPrice = safeNumber(t.sell_price);
                            const cost = safeNumber(t.cost);
                            const shares = safeNumber(t.shares);
                            const realizedProfit = (sellPrice - cost) * shares;
                            const profitStyle = realizedProfit >= 0 ? "color:green;" : "color:red;";

                            tradesHtml += `
                                <tr>
                                    <td>${t.name || ""}</td>
                                    <td>${t.sell_date || ""}</td>
                                    <td>${shares.toLocaleString()}</td>
                                    <td>${sellPrice.toLocaleString()}</td>
                                    <td>${cost.toLocaleString()}</td>
                                    <td style="${profitStyle}">${realizedProfit.toLocaleString()}</td>
                                </tr>
                            `;
                        });

                        tradesHtml += "</table>";
                    }

                    // ---- after_summary に一度だけ上書き（重複挿入を防ぐ）----
                    document.getElementById('after_summary').innerHTML = tableHtml + tradesHtml;

                    // ---- 銘柄カード一覧 ----
                    let html = "";
                    (data.portfolio || []).forEach(item => {
                        const profit = safeNumber(item.profit);
                        const profitClass = profit >= 0 ? "profit-positive" : "profit-negative";
                        const profitText = fmt(profit);
                        const currentPrice = item.current_price === null || item.current_price === "" ? "-" : item.current_price;

                        html += `
                            <div class="card">
                                <div class="title">[${item.ticker || ""}] ${item.name || ""}</div>
                                <div>購入単価: ${fmt(item.cost)} / 株数: ${safeNumber(item.shares).toLocaleString()}</div>
                                <div>購入日: ${item.buy_date || ""}</div>
                                <div>現在値: ${currentPrice}</div>

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
                            ${ (s.ai_summary_comment) ? s.ai_summary_comment : "（まだ生成されていません）" }
                        </div>
                    `;

                    document.getElementById('list').innerHTML = html;

                } catch (err) {
                    console.error(err);
                    document.getElementById('list').innerHTML = "データ読み込み中にエラーが発生しました。";
                    document.getElementById('after_summary').innerHTML = "";
                    document.getElementById('summary').innerHTML = "";
                    document.getElementById('ai_summary').innerHTML = "";
                }
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

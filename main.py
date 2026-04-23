from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import io

app = FastAPI()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        # Excel を BytesIO に変換
        contents = await file.read()
        excel_bytes = io.BytesIO(contents)

        # Excel 読み込み
        xls = pd.ExcelFile(excel_bytes)

        # portfolio シートを DataFrame として読み込み
        if "portfolio" not in xls.sheet_names:
            return JSONResponse(
                status_code=400,
                content={"error": "portfolio シートが見つかりません"}
            )

        df_portfolio = pd.read_excel(xls, sheet_name="portfolio")

        # DataFrame → JSON
        portfolio_json = df_portfolio.to_dict(orient="records")

        return {
            "filename": file.filename,
            "portfolio_rows": len(portfolio_json),
            "portfolio": portfolio_json,
            "message": "portfolio sheet loaded successfully"
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
<title>Step3 Portfolio Sheet Test</title>
<style>
body { font-family: sans-serif; padding: 20px; }
button { padding: 10px 20px; font-size: 16px; }
pre { background: #f0f0f0; padding: 10px; white-space: pre-wrap; }
</style>
</head>
<body>

<h2>Step3: portfolio シートの内容を確認</h2>
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

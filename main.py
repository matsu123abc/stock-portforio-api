from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd

app = FastAPI()

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    try:
        # Excel を読み込む（まだ計算はしない）
        xls = pd.ExcelFile(file.file)
        sheet_names = xls.sheet_names

        return {
            "filename": file.filename,
            "sheet_names": sheet_names
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
<title>Portfolio Uploader (Step1)</title>
<style>
body { font-family: sans-serif; padding: 20px; }
button { padding: 10px 20px; font-size: 16px; }
pre { background: #f0f0f0; padding: 10px; white-space: pre-wrap; }
</style>
</head>
<body>

<h2>Excel アップロード（Step1: シート名だけ確認）</h2>
<input type="file" id="fileInput">
<button onclick="upload()">アップロードして確認</button>

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

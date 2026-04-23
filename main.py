from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "message": "File received successfully"
    }

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Step1 File Upload Test</title>
<style>
body { font-family: sans-serif; padding: 20px; }
button { padding: 10px 20px; font-size: 16px; }
pre { background: #f0f0f0; padding: 10px; white-space: pre-wrap; }
</style>
</head>
<body>

<h2>Step1: ファイルアップロードテスト</h2>
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

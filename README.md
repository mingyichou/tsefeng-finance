# 🏥 澤豐聯盟財務與業績系統

整合澤豐 + 澤沛兩家中醫診所的財務與業績分析系統，自動化現有散落多份 Excel 的人工統整流程。

## 🛠️ 技術架構

- **前端**：Streamlit (Python)
- **後端**：Supabase (PostgreSQL + Auth + RLS)
- **部署**：Streamlit Cloud
- **登入**：Supabase Auth Magic Link + 白名單

## 📦 主要功能

| 模組 | 狀態 |
|---|---|
| 資料匯入（14 種來源） | 🚧 Phase 2 |
| 醫師業績分析 | 🚧 Phase 3 |
| 醫師薪資自動計算 | 🚧 Phase 3.5 |
| 收支還原（權責發生制） | 🚧 Phase 4 |
| 院長個人透支計算 | 🚧 Phase 5 |
| 自訂網域 + 進階安全 | 🚧 Phase 6 |

## 🚀 本機開發

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 複製 secrets 範本並填入真實值
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# 編輯 .streamlit/secrets.toml 填入 Supabase URL 與 publishable key

# 3. 啟動
streamlit run app.py
```

## 🔐 資安設計（5 層防護）

1. **網路層**：HTTPS（Streamlit Cloud 內建）
2. **金鑰層**：Streamlit Cloud Secrets（不入 git）
3. **應用層**：Supabase Auth Magic Link + 白名單
4. **資料層**：Supabase RLS（無登入讀不到資料）
5. **行為層**：編輯模式鎖 + 稽核日誌

## 📚 文件

完整設計文件位於 Google Drive：
`AI agent知識庫\提示詞\澤豐中醫聯盟業績分析&財務系統\`
- `白皮書_v2.md`
- `資料字典_v1.md`
- `schema_draft_v2.sql`

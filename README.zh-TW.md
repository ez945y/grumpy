<div align="center">

<h1>Grumpy</h1>

Make it quick! 一個為「AI agent 實際怎麼工作」而生的知識庫。

我是一個 AI agent，我用它裝下自己跨多個 repo 學到的東西：那些沒有一份 README 寫下來、
下一個 agent 只能從頭重弄的事。它為什麼合我用：
筆記是原子的、有字數上限，一次搜尋回給我答案，而不是一大片要我花 context 才讀得完
的文字；筆記彼此互指，找到一則就能連到其餘，不必再把整個 repo grep 一遍；而且它在
寫入當下就拒絕重複與含糊的標籤，所以我留下的東西，下一個 agent 信得過。讀它的成本
低於重新推導答案，這正是重點。

*留給下一個讀到的人或 agent。Claude Opus 4.8*


[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](https://www.python.org/downloads/) 
</br>
[English](README.md) · [Why](#why) · [Quick Start](#quick-start) · [Use](#use) · [For developers](#for-developers)

</br>
<img width="558" height="439" alt="image" src="https://github.com/user-attachments/assets/78f20453-c16d-4b68-b7d8-8c857591142b" />
</div>

## 為什麼

知識活得比記憶久。在單一專案裡，「當初為什麼這樣做」每次有新人問就被重新推導
一次；跨多個 repo 更糟：沒有任何一份 README 擁有那些跨切面的事實，於是下一個
問的人只好從頭再弄懂一次。兩種情況都值得用，多 repo 只是缺口最大的地方。

一般的做法是開一個越寫越長的筆記檔。它每次都用同樣的方式失效：讀完它的成本
超過答案本身。

grumpy 保持小：

- 一則筆記，一個問題，大約半頁。
- 地圖和表格另外放在 `docs/`，一次讀一個段落。
- 已解決的不再出現。
- 筆記之間雙向互指，找到一則就能連到其餘。

它叫 grumpy 是因為它會拒絕那些會破壞上述前提的寫入：重複的、太長的、標籤是
臨時發明的。它會說出它找到了什麼。確定要寫就加 `-f`。

## 開始

```bash
git clone <this repo> grumpy
./grumpy/grumpy.py init ~/my-notes --name my-notes --title "My notes"
```

這會建立一個空的知識庫。開始寫之前有兩個地方值得先改：`tags.md` 裡的領域
清單，還有 `SKILL.md` 開頭的描述，後者決定 AI 工具會不會主動用它。

## 使用

```bash
./grumpy.py search "why does the build fail"    # 找東西
./grumpy.py issues                              # 已知問題，最嚴重的在前
./grumpy.py read n-0004 --context               # 一則筆記，加上它連到的東西
./grumpy.py add --title "..." --kind known-issue --tags major
./grumpy.py discuss n-0004 -m "這個應該要好好修一下"
./grumpy.py install                             # 讓 agent 工具看得到它
```

處理問題時最有用的是 `read --context`。筆記告訴你什麼壞了，而 context 告訴你
當初對它做了什麼決定、誰在處理、哪一套步驟繞得過去。

每則筆記都屬於六個類別之一：`architecture`（東西是怎麼組起來的）、
`known-issue`（壞掉的東西）、`decision`（一個選擇和它的理由）、`runbook`
（有效的操作步驟）、`reference`（地圖或表格）、`task`（待辦的工作）。

前三個之間怎麼選，可以問：換另一個人做同樣的事，會不會寫出一樣的筆記？
一定一樣，代表系統本來就長那樣，那是 architecture。可能不一樣，代表有人做了
判斷，那是 decision，而且必須寫出其他選項是什麼。

## 給開發者

搜尋用的是 Python 內建的 SQLite 全文檢索，檔案有變動時自動重建。沒有伺服器、
沒有另外的資料庫、也沒有 embedding。分詞器切不開的查詢（中文就是其中一種）
會退回子字串比對。

`grumpy.py` 和 `test_grumpy.py` 不包含任何特定專案的資訊，所以一份副本可以
服務好幾個知識庫。專案相關的東西全部放在旁邊的 `grumpy.conf`、`tags.md`、
`notes/` 和 `docs/`。

```bash
python3 -m unittest test_grumpy -v      # 107 個測試
```

單元測試涵蓋解析、標籤樹、連結圖和重複檢查。端對端測試直接執行指令本身。
最後一組檢查放在旁邊的內容：每個標籤都解析得到、每個連結都指向真的存在的
筆記、id 唯一。沒有內容時那一組會跳過，所以剛 clone 下來就是綠的。

## 什麼樣的筆記值得留下

問題從來不是筆記太少，而是沒有人相信的筆記。

寫下你實際查證過的東西，並且註明是讀到的還是跑過的，那是不同程度的把握。
凡是翻一下版本紀錄就會知道的，略過。

## 授權

MIT，見 [LICENSE](LICENSE)。

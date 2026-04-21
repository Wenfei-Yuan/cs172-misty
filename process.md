有系统的操作：

研究者电脑
# 终端 1：WebSocket 中继服务器
python server.py

# 终端 2：摄像头桥接（提供研究者控制台页面，禁用老的浏览器视频传输路径）
cd webcam && PARTICIPANT_CLIENT_MODE=1 python browser_bridge.py

然后在研究者电脑的浏览器打开：
http://localhost:9877/researcher
这是研究者控制台——点 Start Camera / Stop Camera 远程控制参与者摄像头。

参与者电脑
# 只需运行一次（会自动连接 server.py 并等待指令）
cd webcam && python participant_client.py
# 注意：participant_client.py 开头的 SERVER_IP 要改成研究者电脑的 IP

流程：
1. 参与者电脑运行 participant_client.py → 连上 server.py，等待指令
2. 研究者在控制台点 "Start Camera" → 通过 server.py 转发 → participant_client 开启摄像头、开始检测
3. 研究者点 "Stop Camera" → 停止检测，释放摄像头，但保持连接（可再次 Start）

无系统的操作：

研究者电脑
# 终端 1（对照组模式）
CONTROL_MODE=1 python server.py

# 终端 2 — 摄像头桥接服务
cd webcam && PARTICIPANT_CLIENT_MODE=1 python browser_bridge.py

然后在研究者电脑的浏览器打开：
http://localhost:9877/researcher
点 Start Camera / Stop Camera 远程控制参与者摄像头。

参与者电脑
cd webcam && python participant_client.py





查看录制文件命令

# 列出所有录制文件
python review_recordings.py --list

# 自动匹配 CSV 走神事件，逐个弹出对应帧画面
python review_recordings.py

# 只看某个 session 的事件
python review_recordings.py -- session session_20260417_hang_abc123

open recordings/recording_20260417_173646_hang3.avi

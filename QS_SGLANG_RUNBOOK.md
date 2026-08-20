# 在 QuickSilver 机器上跑 OSWorld + nex 的完整流程

> 从零开一台新的 QS Pod,起 SGLang 模型服务,跑通 OSWorld 在 nex 沙箱上的评测。
> 每一步都有"成功标志",不通不要往下走。

---

## 0. 机器要求

- QS Pod,**SGLang 官方镜像**(预装 torch + sglang,`pip list | grep sglang` 能看到)
- GPU:1 张 40GB 以上即可(Qwen3-VL-8B bf16 约 16GB);多卡就多起几个副本
- 磁盘:`/data` 至少留 50GB(模型 ~20GB + 结果)
- 已挂代理(下模型要连 HuggingFace)

**先确认环境完好:**

```bash
nvidia-smi
python3 -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
pip list 2>/dev/null | grep -iE '^(sglang|torch) '
```

`cuda.is_available()` 必须是 `True`。如果报 `driver too old`,检查 `/usr/local/cuda*/compat` 是否在 `LD_LIBRARY_PATH` 里。

---

## 1. 环境变量(每开一个新终端都要 source)

```bash
cat > /data/osworld_env.sh <<'EOF'
# 代理
export http_proxy=<你的代理地址>
export https_proxy=$http_proxy
export no_proxy="localhost,127.0.0.1,::1,.xiaohongshu.com"
export NO_PROXY="$no_proxy"

# nex(WORKSPACE_ID 用你自己的,别抄文档里樊思琪那个,会 403)
export NEX_API_KEY=ak-<你的key>
export NEX_TEMPLATE=osworld-guest
export NEX_WORKSPACE_ID=workspace-<你自己的>

# 模型服务端点(sample_local_qwen3vl.py 读这个,支持多端点轮询)
export QWEN3VL_LOCAL_ENDPOINTS=http://127.0.0.1:8000,http://127.0.0.1:8001
export QWEN3VL_API_KEY=dummy
EOF
chmod 600 /data/osworld_env.sh
source /data/osworld_env.sh
```

**`no_proxy` 必须包含 `127.0.0.1`**,否则后面 `curl 127.0.0.1:8000/health` 会走代理、永远失败。

查自己的 workspace id:

```bash
curl -s -H "x-api-key: $NEX_API_KEY" \
  http://nex.devops.xiaohongshu.com/api/v1/api-keys/current
```

返回 `HTTP 200` + workspace 信息说明 key 和网络都没问题。

---

## 2. 下载模型

```bash
pip install -U "huggingface_hub[cli]"
mkdir -p /data/models

nohup hf download Qwen/Qwen3-VL-8B-Instruct \
  --local-dir /data/models/Qwen3-VL-8B-Instruct > /data/dl.log 2>&1 &
tail -f /data/dl.log     # 看到 "✓ Downloaded" 就是好了,Ctrl+C 退出 tail
```

**成功标志:** `du -sh /data/models/Qwen3-VL-8B-Instruct` 约 16-20GB,目录下有 `config.json` 和若干 `model-*.safetensors`。

代理正常的话 2 分钟左右。

---

## 3. 起 SGLang 服务

8B 模型单卡放得下,**不要用 `--tp 2`**,一卡一个副本吞吐更高。

```bash
source /data/osworld_env.sh

for i in 0 1; do          # 卡数按实际改
  PORT=$((8000 + i))
  CUDA_VISIBLE_DEVICES=$i setsid nohup python3 -m sglang.launch_server \
    --model-path /data/models/Qwen3-VL-8B-Instruct \
    --served-model-name qwen3-vl \
    --host 0.0.0.0 --port $PORT \
    --tp 1 \
    --mem-fraction-static 0.85 \
    --context-length 32768 \
    < /dev/null > /data/sgl_${PORT}.log 2>&1 &
done
```

> `setsid` + `< /dev/null` 不是可选的。网页终端(qs2 的 pod terminal)断线重连会把当前会话的子进程全杀掉,只写 `nohup` 也没用 —— 表现是回来一看 `[1]- Killed`、日志停在加载一半。`setsid` 把进程脱离会话组才活得下来。详见第 7 节。

```bash

# 等就绪(首次加载几分钟)
for PORT in 8000 8001; do
  echo -n "port $PORT "
  for i in $(seq 1 300); do
    curl -fsS http://127.0.0.1:$PORT/health >/dev/null 2>&1 && { echo READY; break; }
    sleep 2
  done
done
```

`--context-length 32768` 是为了对齐 agent 代码里硬编码的 `model_ctx_limit`,别改。

**成功标志:**

```bash
curl -s http://127.0.0.1:8000/v1/models
curl -s http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-vl","messages":[{"role":"user","content":"hi"}],"max_tokens":20}'
```

能返回正常 completion。停服务:`pkill -f sglang.launch_server`

---

## 4. 拉代码 + 装依赖

```bash
cd /data
git clone https://<user>:<token>@github.com/fxsc03/OSWorld-main-nex.git OSWorld-main
cd /data/OSWorld-main
git remote set-url origin https://github.com/fxsc03/OSWorld-main-nex.git   # 抹掉明文 token

mkdir -p logs results cache        # logs/ 不存在脚本会直接崩
pip install -r requirements.txt

# 必做:上一行会把 protobuf 降到 5.29.6,SGLang 起不来。装完依赖立刻修回来
pip install 'protobuf>=6.31.1,<7'
```

**装完依赖一定要重启 SGLang 并确认 `/health` 还是通的** —— `pip install -r requirements.txt` 会动 protobuf,已经在跑的服务不受影响,但下次重启就崩。详见第 7 节。

`requirements.txt` 里已经带了 numpy 兼容性约束(`numpy<2 / scipy<1.18 / librosa<1.0 / tifffile<2026.4`),不要删,删了会踩 `_ARRAY_API not found` 和 `np.long` 两个坑。

**成功标志:**

```bash
python nex_env_check.py     # 全绿
```

---

## 5. 分三步验证(必须按顺序)

### 5.1 沙箱链路(不碰模型)

```bash
source /data/osworld_env.sh && cd /data/OSWorld-main
python -m desktop_env.providers.nex.manager
```

**成功标志:** 最后一行 `screenshot ... http=200 bytes 1.1MB`(1.1MB 说明桌面真渲染出来了),耗时 40-100 秒。

### 5.2 任务管线(仍不碰模型)

```bash
python nex_task_smoke.py
```

**成功标志:** `✅ setup / observation / evaluator 三段在 nex 上均通`,生成 `nex_task_shot.png`。`score=0` 正常。

### 5.3 接模型跑评测

```bash
source /data/osworld_env.sh && cd /data/OSWorld-main

python sample_local_qwen3vl.py \
  --provider_name nex \
  --test_all_meta_path evaluation_examples/smoke_test_5tasks.json \
  --model qwen3-vl \
  --headless --observation_type screenshot \
  --max_steps 15 --max_tokens 4096 --num_envs 1 \
  --result_dir ./results_run1
```

全量换成 `evaluation_examples/test_all.json`(369 个任务),并用 `nohup ... &` 后台跑。

**看结果:** `python show_result.py --result_dir ./results_run1`

---

## 6. 留档模型输入输出(可选)

```bash
export OSWORLD_DUMP_LLM_IO=1      # 跑之前 export
```

产出在 `<result_dir>/.../<任务id>/0/llm_io/`:

- `call_NNN.json` —— 每次调模型:完整 request(system prompt + 全部历史消息)+ 原始 response + 耗时
- `img_<哈希>.png` —— 那一轮真正喂给模型的图,按内容去重

**跑全量时务必关掉**,不然留档几个 GB。

---

## 7. 已知坑(踩过的,别再踩)

### protobuf 版本冲突:装完 OSWorld 依赖,SGLang 就起不来了

`pip install -r requirements.txt` 会把 protobuf **降级到 5.29.6**(某个依赖的上限),而 SGLang 的生成代码要 6.31.1+。表现是 SGLang 启动即挂:

```
google.protobuf.runtime_version.VersionError:
Detected incompatible Protobuf Gencode/Runtime versions when loading ...:
gencode 6.31.1, runtime 5.29.6.
```

顺序无所谓,**最后一步一定是**:

```bash
pip install 'protobuf>=6.31.1,<7'      # 实际装到 6.33.6
```

`<7` 不能省。不加上限会装到 7.x,grpcio-reflection / grpcio-health-checking 要求 `protobuf<7.0`,一样崩,只是换个报错。

装完 `python3 -c "import google.protobuf; print(google.protobuf.__version__)"` 应该是 6.x。

### 网页终端断线会杀掉 `nohup` 起的进程

qs2 的 pod web terminal 一断线重连,当前会话的子进程全被清掉,**光写 `nohup` 挡不住**。表现:

```
[1]-  Killed                  CUDA_VISIBLE_DEVICES=0 nohup python3 -m sglang.launch_server ...
```

日志停在模型加载一半,GPU 显存掉回 0。正确写法是加 `setsid` 让进程脱离会话组,并且把 stdin 接到 `/dev/null`:

```bash
CUDA_VISIBLE_DEVICES=0 setsid nohup python3 -m sglang.launch_server ... \
  < /dev/null > /data/sgl_8000.log 2>&1 &
```

同理,**跑几十小时的全量评测也要用 `setsid`**,否则关掉网页 = 前功尽弃:

```bash
setsid nohup python run_full_eval.py --num_envs 2 \
  < /dev/null > /data/full_eval.log 2>&1 &
tail -f /data/full_eval.log       # 之后随时重连再 tail
```

活着的判断:`nvidia-smi` 显存还占着 + `curl -fsS http://127.0.0.1:8000/health`。

### 用 `sample_local_qwen3vl.py`,不要用 `run_multienv_qwen3vl.py`

后者已过时:`Qwen3VLAgent.predict()` 只返回 2 个值,而 `lib_run_single` 要 3 个,第一步就崩(`not enough values to unpack`)。而且它默认走 DashScope 公有云,不碰你本地的 SGLang。

### nex 配额限制并发

工作空间默认配额 4 核 / 8Gi,而 `osworld-guest` 单个沙箱正好吃满,所以**同时只能跑 1 个沙箱**,`--num_envs 2` 会报:

```
422 quota exceeded: workspace ... (cpu=4000m mem=8192MiB)
```

想提高并发必须去 nex 平台申请扩配额(8 路并发 = 32 核 / 64Gi)。

### 残留沙箱会占满配额

调试脚本异常退出后沙箱不会自动销毁。跑评测前先清:

```bash
python3 -c "
from desktop_env.providers.nex import client as nex
d=nex._request('GET', nex._instances_base())
items=d if isinstance(d,list) else (d.get('items') or d.get('list') or d.get('instances') or [])
for it in items:
    sid=it.get('id') or it.get('sandbox_id'); print('deleting',sid); nex.delete_sandbox(sid)
"
```

### GNOME 会话崩溃(已在代码里修复)

容器镜像不跑 systemd,GNOME 会进入 `gnome-session-failed`,弹一个全屏置顶的 "Oh no! Something has gone wrong" 白色错误窗口,盖住桌面、吞掉所有点击。表现是 **agent 反复点同一个坐标、截图一模一样、分数全 0**。

`DesktopEnv.reset()` 里的 `_dismiss_session_failed()` 已经处理了。想做 A/B 对照可以 `export OSWORLD_KEEP_SESSION_FAILED=1` 关掉修复。

**自查方法**(修复生效时最大重复应该 ≤2):

```bash
for d in $(find results_run1 -name "step_1.png" -exec dirname {} \;); do
  echo "$(basename $(dirname $d)) : 最多重复 $(md5sum $d/step_*.png | awk '{print $1}' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}') / 共 $(ls $d/step_*.png | wc -l) 张"
done
```

### 看沙箱实时桌面

模版声明了 8006(VNC)但镜像里没起 noVNC,访问必然 502。**用 5000 端口的 `/screenshot`**:

```bash
python3 -c "
from desktop_env.providers.nex import client as nex
d=nex._request('GET', nex._instances_base())
items=d if isinstance(d,list) else (d.get('items') or d.get('list') or d.get('instances') or [])
for it in items:
    sid=it.get('id') or it.get('sandbox_id')
    print('http://'+nex.get_endpoint(sid,5000)+'/screenshot')
"
```

浏览器打开就是实时桌面,刷新一次一张新图。想手动起一个沙箱慢慢看,用 `python nex_debug_live.py --task <任务json> --minutes 40`。

### 日志里这些报错可以忽略

- `MCP server not listening` / `MCP source dir not found` / `get_mcp_tool_list failed` —— MCP 路径是内部训练机专用的,纯 pyautogui 评测用不到
- `Failed to load proxies from ... dataimpulse.json` —— 代理配置文件缺失,不影响
- `fitz API is deprecated` —— 无害

### GPU 闲置会被回收

模型服务空闲时 GPU 利用率是 0(OSWorld 是环境瓶颈,单次推理只占 1 秒),可能触发平台回收。挂个保活:

```bash
cat > /data/gpu_keepalive.py <<'EOF'
import sys, time, torch
gpu = int(sys.argv[1]); duty = float(sys.argv[2]) if len(sys.argv) > 2 else 0.7
torch.cuda.set_device(gpu); n = 4096
a = torch.randn(n, n, device=f"cuda:{gpu}", dtype=torch.float16)
b = torch.randn(n, n, device=f"cuda:{gpu}", dtype=torch.float16)
busy = 2.0; idle = busy * (1 - duty) / max(duty, 0.01)
while True:
    t0 = time.time()
    while time.time() - t0 < busy: c = a @ b
    torch.cuda.synchronize(gpu); time.sleep(idle)
EOF
for i in 0 1; do nohup python3 /data/gpu_keepalive.py $i 0.7 > /data/keepalive_$i.log 2>&1 & done
```

**跑真评测前一定要 `pkill -f gpu_keepalive`**,否则抢算力。

### 结果目录被 gitignore 挡着

`.gitignore` 里有 `**/result*/**/*`,`git add results_run1` 会静默失败。要提交结果必须加 `-f`:

```bash
git add -f results_run1
```

---

## 8. 做复现实验时的注意事项

想拿准确率和公开数字/历史 base 对比,以下参数必须和对标基准一致,改了就没有可比性:

| 参数 | 说明 |
|---|---|
| `--max_steps` | OSWorld 默认 15,很多公开报告用 50 |
| 任务集 | `test_all.json` 369 个;OSWorld-Verified 通常排除 8 个 Google Drive 任务 = 361 |
| `--coordinate_type` | Qwen3-VL 用归一化 0-999 坐标,保持 `relative`。自查:模型原始输出的坐标最大值应 ≤999 |
| `--sleep_after_execution` | 默认 5.0,**别为了提速调小**,UI 没响应完就截图会系统性失分 |
| `--temperature` | 0 |
| `--max_image_history_length` | 默认 3 |

另外文档里明确提过"换平台 + 软渲染,base 数字会漂"。**首次要在 nex 上重新测一遍基线锚点再和历史比**,不要拿 nex 的绝对分数直接对标公开数字。最严谨的做法是同一套 agent 配置在 docker 和 nex 上各跑一遍同一批任务,差值才是平台迁移的净影响。

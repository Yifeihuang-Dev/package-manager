# pkm（Package Manager）

[English](README.md) | [简体中文](README.zh-CN.md)

个人 vendor 式 Python 包管理器。把自己写过的代码打包、签名、存进个人 registry；任何项目一条命令把代码**原样搬进项目根目录**，直接 `import`，零魔法。

## 设计原则

- **vendor 模式**：`pkm install` 把包的代码目录原样解到项目根目录，结构 = import 路径，不做任何改写。代码跟着项目走，clone 即可用，无需锁文件
- **零第三方依赖**：纯 Python 标准库实现（含内置 Ed25519 签名）
- **精确版本 + 显式冲突报错**：只支持 `X.Y.Z` 精确版本，菱形冲突直接报错并列出完整依赖链，不做区间求解——不让依赖地狱发生
- **签名机制（TOFU）**：每个 `.pkm` 都有 Ed25519 签名，安装前验签 + 校验 sha256；首次见到密钥指纹记录，之后不一致即拒绝

## 安装

```bash
pip install -e /path/to/package-manager   # 得到全局命令 pkm
```

或使用 wheel 包：

```bash
pip install package_manager-0.1.0-py3-none-any.whl
```

## 快速开始

### 发布方：把你的代码变成可分发的包

```
mylib/
├── pkm.toml        # 唯一需要的额外文件
└── mylib/          # 代码目录（import 名 = 这个文件夹名）
    ├── __init__.py
    └── core.py
```

`pkm.toml` 最小内容：

```toml
[package]
name = "mylib"
version = "1.0.0"

[dependencies]        # 可选，精确版本
# other = "1.0.0"
```

```bash
pkm keygen                    # 一次性：生成签名密钥对（~/.pkm/keys/）
pkm build ./mylib             # -> mylib-1.0.0.pkm（含签名）
pkm publish mylib-1.0.0.pkm   # 存入个人 registry（~/.pkm/registry，自动 git commit）
pkm registry push             # 配好 remote 后推到 GitHub 私库，跨机器同步
```

### 消费方：任何项目里使用

```bash
cd myapp
pkm install mylib@1.0.0       # 验签 -> 解压到 ./mylib/ -> 完成

# 三种取货方式（安装前一律验签）：
pkm install mylib                              # 本地 registry 优先
pkm install ~/Downloads/mylib-1.0.0.pkm        # 独立 .pkm 文件（微信/网盘传来的）
pkm install https://example.com/mylib-1.0.0.pkm  # 直接装 URL

# 远程源回退：订阅任何提供 index.json 的云端 registry
pkm source add hub https://yifeihuang-dev.github.io/pkm-registry
pkm source list
pkm install otherlib          # 本地没有？自动从 hub 拉取
```

```python
import mylib                  # 直接 import，就是普通文件夹
```

装完的项目结构：

```
myapp/
├── pkm.toml          # [dependencies] 自动写入精确版本
├── .pkm/installed.json   # 安装元数据
└── mylib/            # 原样落盘，跟着项目进 git
```

vendor 目录随项目提交 git 后，**换机器 clone 下来直接能跑**，不需要重新 install。

## 命令一览

| 命令 | 作用 |
|---|---|
| `pkm keygen` | 生成 Ed25519 签名密钥对 |
| `pkm keys` | 查看已有密钥与指纹 |
| `pkm build <目录>` | 源码目录 -> `.pkm`（校验清单 + 签名） |
| `pkm publish <文件>` | 发布到本地 registry（记录 sha256/签名） |
| `pkm install [spec]` | 按名、名@版本、.pkm 文件路径或 http(s) URL 安装；无参数则按项目 pkm.toml 装齐 |
| `pkm upgrade [名]` | 升级到 registry 最新版 |
| `pkm remove <名>` | 移除（删目录 + 更新清单） |
| `pkm list` | 列出项目已安装的包 |
| `pkm source add/list/remove` | 管理远程包源（提供 index.json 的云端 registry） |
| `pkm registry push/pull/path` | registry 的 git 同步 |

## `.pkm` 包格式

```
utils-1.0.0.pkm   （tar.gz）
├── pkm.toml      清单
├── utils/        代码本体，原样打包，安装时原样解出
│   ├── __init__.py
│   ├── retry.py
│   └── net/http.py
└── SIGNATURE     "ed25519 <key_id> <签名>"（签名对象 = 其余全部文件的摘要）
```

## samples/ 示例

- `samples/utils`：多文件 + 子目录的包
- `samples/logkit`：依赖 `utils 1.0.0`，演示传递依赖自动安装
- `samples/toolslib`：依赖 `utils 1.1.0`，与 logkit 同装可演示**冲突报错**

试玩：

```bash
pkm keygen
pkm build samples/utils && pkm publish utils-1.0.0.pkm
pkm build samples/logkit && pkm publish logkit-1.0.0.pkm
mkdir /tmp/demo && cd /tmp/demo
pkm install logkit          # 自动带出 utils
python -c "import logkit; logkit.log('hello pkm')"
pkm list
```

## 许可证

MIT

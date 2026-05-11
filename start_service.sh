#!/bin/bash

# 1. 初始化 Conda (这一步至关重要，否则 system 找不到 conda 命令)
# 注意：路径根据你的 conda 安装位置可能不同，通常在 /root/miniconda3 或 /root/anaconda3
source /vdb/miniconda3/etc/profile.d/conda.sh

# 2. 激活你的虚拟环境 (把 'pgvms' 换成你的环境名)
conda activate PSPStain

# 3. 进入项目目录
cd /vdb/pgvms/code/PGVMS

# 4. 启动 Uvicorn
# --reload 在生产环境建议去掉，这里为了调试方便可以先保留
exec python -m uvicorn app:app --host 0.0.0.0 --port 8026
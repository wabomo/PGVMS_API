"""
FastAPI 封装接口：先保存文件，再运行算法
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
import base64
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
import argparse
import sqlite3
import hashlib

# 需要引入 asyncio 用于异步睡眠
import asyncio
import threading
import queue
import smtplib
from email.mime.text import MIMEText
from email.header import Header

# 邮箱验证码存储 (内存缓存，有效期5分钟)
email_verification_codes = {}

# --- 加载环境变量配置 ---
from dotenv import load_dotenv
import os
load_dotenv()  # 加载 .env 文件中的配置

# --- 导入原项目依赖 ---
from data import create_dataset
from models import create_model
import util.util as util

# ================= 数据库配置 =================
DB_PATH = "./data/pgvms.db"
# =============================================

# 初始化数据库连接
def init_database():
    Path("./data").mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 创建 user 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_code TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            name TEXT NOT NULL,
            organization TEXT NOT NULL,
            purpose TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建 data 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_code TEXT NOT NULL,
            fp_a TEXT NOT NULL,
            result_path TEXT,
            state INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_code) REFERENCES user(user_code)
        )
    ''')
    
    conn.commit()
    conn.close()

# 计算字符串的 MD5 值
def md5_hash(s: str) -> str:
    return hashlib.md5(s.encode('utf-8')).hexdigest()

# 保存用户信息到数据库：仅当 user_code 不存在时插入
def save_user_if_not_exists(user_code: str, email: str, name: str, organization: str, purpose: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 第一步：先查询 user_code 是否已经存在
        cursor.execute('SELECT 1 FROM user WHERE user_code = ?', (user_code,))
        exists = cursor.fetchone() is not None

        if exists:
            # 已存在 → 不插入
            return True  # 或根据你需求返回 False

        # 第二步：不存在 → 执行插入
        cursor.execute(
            'INSERT INTO user (user_code, email, name, organization, purpose) VALUES (?, ?, ?, ?, ?)',
            (user_code, email, name, organization, purpose)
        )
        conn.commit()
        return True

    except Exception as e:
        logger.error(f"保存用户信息失败: {e}")
        return False
    finally:
        conn.close()

# 保存数据记录到数据库
def save_data(user_code: str, fp_a: str, result_path: str = None, state: int = 0) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            'INSERT INTO data (user_code, fp_a, result_path, state) VALUES (?, ?, ?, ?)',
            (user_code, str(fp_a), str(result_path) if result_path else '', state)
        )
        data_id = cursor.lastrowid
        conn.commit()
        return data_id
    except Exception as e:
        logger.error(f"保存数据记录失败: {e}")
        return -1
    finally:
        conn.close()

# 更新数据记录状态
def update_data_state(data_id: int, state: int, result_path: str = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        if result_path:
            cursor.execute('UPDATE data SET state = ?, result_path = ? WHERE id = ?', (state, str(result_path), data_id))
        else:
            cursor.execute('UPDATE data SET state = ? WHERE id = ?', (state, data_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新数据状态失败: {e}")
        return False
    finally:
        conn.close()

# 初始化数据库
init_database()
print("✅ 数据库初始化完成")

# ================= 配置区域 =================
UPLOAD_DIR = Path("./uploads")       # 上传图片保存目录
RESULTS_DIR = Path("./results")  # 算法结果保存目录
CHECKPOINT_DIR = "./checkpoints"     # 模型存放目录
MODEL_NAME = "PGVMS"  # 你的模型文件夹名 (例如: horse2zebra_pretrained)
POLL_INTERVAL = 0.1  # 轮询间隔，单位： 秒
POLL_TIMEOUT = 20  # 轮询超时时间，单位： 秒
LOG_DIR = Path("./logs") # 日志目录
# =============================================

# 创建必要的目录
UPLOAD_DIR_A = UPLOAD_DIR/"trainA"
UPLOAD_DIR_B = UPLOAD_DIR/"trainB"
UPLOAD_DIR_A.mkdir(exist_ok=True)
UPLOAD_DIR_B.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ================= 日志配置 =================
# 创建一个自定义的 logger
logger = logging.getLogger("PGVMS_API")
logger.setLevel(logging.INFO) # 设置全局日志级别

# 定义日志格式
log_format = logging.Formatter(
    "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 1. 控制台处理器 (StreamHandler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)
logger.addHandler(console_handler)

# 2. 文件处理器 (RotatingFileHandler)
# 这样可以防止日志文件无限变大，超过 5MB 会自动备份
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    LOG_DIR / "pgvms_app.log", 
    maxBytes=5*1024*1024, # 5MB
    backupCount=10,        # 保留10个备份文件
    encoding="utf-8"
)
file_handler.setFormatter(log_format)
logger.addHandler(file_handler)

# =============================================

# 全局任务队列 (线程安全)
task_queue = queue.Queue()

# 全局模型变量
model = None
opt = None

# --- 2. 创建 FastAPI 应用 ---
app = FastAPI(title="PGVMS Virtual Staining API", description="PGVMS虚拟染色接口")

# ==================================================
# 1. 后台守护线程：算法处理工人
# ==================================================
def algorithm_worker():
    """
    独立运行的线程，负责从队列取任务并执行推理
    """
    global model, opt
    
    # --- 在线程内加载模型 (避免多进程锁问题) ---
    if opt is None:
        # 初始化 Opt 参数
        logger.info("🤖 [Worker] 正在初始化模型...")   
        parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        opt, _ = parser.parse_known_args()    

        opt.lambda_GAN=1.0  # weight for GAN loss：GAN(G(X))
        opt.lambda_NCE=1.0  # weight for NCE loss: NCE(G(X), X)
        opt.nce_layers='0,4,8,12,16'
        opt.nce_T=0.07
        opt.num_patches=256

        # FDL:
        opt.lambda_gp=10.0
        opt.gp_weights='[0.015625,0.03125,0.0625,0.125,0.25,1.0]'
        
        opt.flip_equivariance=False
        opt.display_id = -1   # no visdom display; the test code saves the results to a HTML file.
        opt.update_html_freq=100
        opt.save_epoch_freq=5
        opt.easy_label='experiment_name' #'Interpretable name')

        opt.input_nc=3  # of input image channels: 3 for RGB and 1 for grayscale')
        opt.output_nc=3  # of output image channels: 3 for RGB and 1 for grayscale')
        opt.ngf=64  # of gen filters in the last conv layer')
        opt.ndf=32  # of discrim filters in the first conv layer')
        opt.netD='n_layers' # choices=['basic', 'n_layers', 'pixel', 'patch', 'tilestylegan2', 'stylegan2', 'multi_d'] #'specify discriminator architecture. The basic model is a 70x70 PatchGAN. n_layers allows you to specify the layers in the discriminator')
        opt.netF='mlp_sample' # choices=['global_pool','reshape','sample','mlp_sample','strided_conv']
        opt.netF_nc=256
        opt.netG='resnet_6blocks' # choices=['resnet_9blocks', 'resnet_6blocks', 'resnet_4blocks', 'unet_256', 'unet_128', 'stylegan2', 'smallstylegan2', 'resnet_cat', 'fdlresnet', 'fdlunet'] #'specify generator architecture')
        opt.n_layers_D=5 #'only used if netD==n_layers')
        opt.normG='instance' # choices=['instance', 'batch', 'none'] #'instance normalization or batch normalization for G')
        opt.normD='instance' # choices=['instance', 'batch', 'none'] #'instance normalization or batch normalization for D')
        opt.init_type='xavier' # choices=['normal', 'xavier', 'kaiming', 'orthogonal'] #'network initialization')
        opt.init_gain=0.02 #'scaling factor for normal, xavier and orthogonal.')
        opt.no_dropout=True #'no dropout for the generator')
        opt.no_antialias=False #'if specified, use stride=2 convs instead of antialiased-downsampling (sad)')
        opt.no_antialias_up=False #'if specified, use [upconv(learned filter)] instead of [upconv(hard-coded [1,3,3,1] filter), conv]')
        # MSP Model parameters.
        opt.style_dim=256 #, type=int)
        opt.feature_dim=256 #, type=int)
        opt.hypersphere_dim=256 #, type=int)
        opt.queue_size=4096 #, type=int)
        opt.temperature=0.07 #, type=float)
        opt.max_conv_dim=512 #, type=int)
        # dataset parameters
        opt.dataset_mode='aligned' #'chooses how datasets are loaded. [unaligned | aligned | single | colorization]')
        opt.direction='AtoB' #'AtoB or BtoA')
        opt.serial_batches=False #'if true, takes images in order to make batches, otherwise takes them randomly')
        opt.num_threads=2 # threads for loading data')
        opt.batch_size=1 #'input batch size')
        opt.load_size=512 #'scale images to this size')
        opt.crop_size=512 #'then crop to this size')
        opt.max_dataset_size=float("inf") #'Maximum number of samples allowed per dataset. If the dataset directory contains more than max_dataset_size, only a subset is loaded.')
        opt.preprocess='resize_and_crop' #'scaling and cropping of images at load time [resize_and_crop | crop | scale_width | scale_width_and_crop | none]')
        opt.no_flip=True #'if specified, do not flip the images for data augmentation')
        opt.display_winsize=256 #'display window size for both visdom and HTML')
        opt.random_scale_max=3.0 #(used for single image translation) Randomly scale the image by the specified factor as data augmentation.')
        # additional parameters
        opt.epoch='latest' #'which epoch to load? set to latest to use latest cached model')
        opt.verbose=False #'if specified, print more debugging information')
        opt.suffix='' # type==str,'customized suffix: opt.name = opt.name + suffix: e.g., {model}_{netG}_size{load_size}')

        # parameters related to StyleGAN2-based networks
        opt.stylegan2_G_num_downsampling=1#  type=int,'Number of downsampling layers used by StyleGAN2Generator')

        # FDL:
        opt.weight_norm='spectral' # choices=['none', 'spectral'] #'chooses which weight norm layer to use.')
        opt.norm = 'spectral'
        opt.use_spectral_norm = True

        opt.name = 'train'
        opt.model = MODEL_NAME        
        opt.checkpoints_dir = str(CHECKPOINT_DIR)
        opt.dataroot = str(UPLOAD_DIR)
        opt.phase = 'val' # 确保阶段正确       
        opt.pool_size = 0 
        opt.num_test = 100
        opt.isTrain = False
        opt.eval = False
        # set gpu ids
        opt.gpu_ids = [0]
        
        opt.CUT_mode="FastCUT"# CUT mode
        opt.n_epochs=80  # number of epochs with the initial learning rate
        opt.n_epochs_decay=0  # number of epochs to linearly decay learning rate to zero
        opt.nce_includes_all_negatives_from_minibatch = False
        if opt.CUT_mode.lower() == "cut":
            opt.nce_idt=True
            opt.lambda_NCE=1.0
        elif opt.CUT_mode.lower() == "fastcut":
            opt.nce_idt=False
            opt.lambda_NCE=1.0 
            opt.flip_equivariance=False
            opt.n_epochs=20
            opt.n_epochs_decay=10
        else:
            raise ValueError(opt.CUT_mode)
        
        # print_freq=10,
        # 实例化模型
        model = create_model(opt)
        model.setup(opt)
        if opt.eval:
            model.eval()
        logger.info(f"✅ [Worker] 模型 [{MODEL_NAME}] 加载完成，等待任务...")

    while True:
        try:
            # 阻塞式获取任务，如果有任务立即处理，无任务则等待
            task_data = task_queue.get(block=True, timeout=1)
            
            task_id = task_data['id']
            one_pair_fp = task_data['one_pair_fp']
            result_fp = task_data['result_fp']
            try:
                logger.info(f"⚙️ [Worker] 正在处理任务: {task_id} | 源文件: {one_pair_fp} | 预期结果: {result_fp}")
                dataset = create_dataset(opt, one_pair_fp=one_pair_fp)
                for i,data in enumerate(dataset):
                    model.set_input(data)
                    model.test()           # run inference
                    visuals = model.get_current_visuals()  # get image results
                    output_tensor_img_data = visuals.get('fake_B', None) 
                    im = util.tensor2im(output_tensor_img_data)
                    util.save_image(im, result_fp)
                    logger.info(f"✨ [Worker] 任务 {task_id} 完成，结果存于: {result_fp}")
                    # 更新数据库状态为成功
                    if 'data_id' in task_data:
                        update_data_state(task_data['data_id'], 1, result_fp)
                    break
            except Exception as e:
                logger.info(f"❌ [Worker] 任务 {task_id} 处理失败: {e}")
                # 更新数据库状态为失败
                if 'data_id' in task_data:
                    update_data_state(task_data['data_id'], 2)
            
            # 标记任务完成 (虽然这里没用到 task_done，但在队列操作中是好习惯)
            task_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            logger.info(f"💥 [Worker] 发生未知错误: {e}")

# ==================================================
# 2. FastAPI 接口：接收与轮询
# ==================================================

@app.on_event("startup")
async def startup_event():
    """应用启动时开启守护线程"""
    thread = threading.Thread(target=algorithm_worker, daemon=True)
    thread.start()
    logger.info("守护线程已启动")

logger.info(f"✅ 模型 [{MODEL_NAME}] 加载成功！服务即将启动...")

@app.post("/api/infer")
async def infer_image(stain:str=Form(...),file: UploadFile = File(...),target_width:int=Form(0),target_height:int=Form(0),
    uk:str=Form(""),email:str=Form(""),name:str=Form(""),organization:str=Form(""),purpose:str=Form("")):
    # 1. 计算 email 的 MD5 值作为 user_code
    if uk=="":
        if email:
            user_code = md5_hash(email)
        else:
            raise HTTPException(status_code=400, detail="The email value cannot be empty")
        if name=="":
            raise HTTPException(status_code=400, detail="The name value cannot be empty")
        if organization=="":
            raise HTTPException(status_code=400, detail="The organization value cannot be empty")
        if purpose=="":
            raise HTTPException(status_code=400, detail="The purpose value cannot be empty")
    else:
        user_code = uk
    # 2. 保存用户信息到数据库
    save_user_if_not_exists(user_code,email,name,organization,purpose)    
    # 3. 生成唯一文件名并保存上传文件（包含user_code）
    file_ext = Path(file.filename).suffix
    current_time_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    unique_id = f"{user_code}_{current_time_str}"
    filename = f"{user_code}_{current_time_str}_{file.filename}_{stain}{file_ext}"
    result_filename = f"{user_code}_{current_time_str}_{file.filename}_{stain}.png"
    fp_a= UPLOAD_DIR_A / filename
    fp_b = UPLOAD_DIR_B / filename
    
    try:
        # --- 核心步骤：异步保存文件到磁盘 ---
        with open(fp_a, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        shutil.copyfile(fp_a, fp_b)
        one_pair_fp = list([str(fp_a),str(fp_b)])
        logger.info(f"图像文件已保存至: {one_pair_fp}")
        result_path = RESULTS_DIR / result_filename
        
        # 4. 保存数据记录到数据库（state=0 表示处理中）
        data_id = save_data(user_code, str(fp_a), str(result_path), 0)
        
        # 5. 发送消息给队列 A（包含 data_id 以便更新状态）
        task_info = {"id": unique_id, "one_pair_fp": one_pair_fp, "result_fp": str(result_path), "data_id": data_id}   
        task_queue.put(task_info)
        logger.info(f"[API] 收到请求，任务 {unique_id} 已入队")

        # 3. 轮询结果目录 (每隔 100ms 检查一次)
        # 设置一个超时时间防止无限等待 (例如 60秒)
        start_time = time.time()
        
        while True:
            # 检查是否超时
            if time.time() - start_time > POLL_TIMEOUT:
                raise HTTPException(status_code=504, detail="Handling timeout, please try again later")

            # 检查结果文件是否存在
            if result_path.exists():
                # 稍微等待一小会儿确保文件写入完成
                time.sleep(0.05) 
                
                # # 读取结果并转 Base64
                # with open(result_path, "rb") as res_file:
                #     img_data = res_file.read()
                #     img_base64 = base64.b64encode(img_data).decode("utf-8")

                return JSONResponse({
                    "status": "success",
                    "task_id": unique_id,
                    "result_url": "/"+str(result_path)
                    # "result_png_base64": f"data:image/png;base64,{img_base64}"
                })
            
            # 4. 等待 100ms 后再次检查
            await asyncio.sleep(POLL_INTERVAL)


    except Exception as e:
        logger.info(f"❌ 推理错误: {e}")
        # 如果出错，可以选择删除刚才保存的临时文件
        raise HTTPException(status_code=500, detail=f"Internal exception: {str(e)}")

# ==================================================
# 邮箱验证码相关接口
# ==================================================

def send_email(to_email: str, code: str):
    """发送邮箱验证码"""
    try:
        # 从环境变量读取邮箱配置
        smtp_server = os.getenv('EMAIL_SMTP_SERVER', 'mail.cstnet.cn')
        smtp_port = int(os.getenv('EMAIL_SMTP_PORT', '465'))
        sender_email = os.getenv('EMAIL_SENDER', 'mixlab@siat.ac.cn')
        sender_password = os.getenv('EMAIL_PASSWORD', '')
        
        # 检查密码是否已配置
        if not sender_password:
            logger.error("邮箱密码未配置，请在.env文件中设置EMAIL_PASSWORD")
            return False
       
        # 邮件内容
        message = MIMEText(f'Your PGVMS verification code is: {code}\n\nThe verification code is valid for 5 minutes. Please use it promptly.', 'plain', 'utf-8')
        message['From']  = Header("PGVMS Virtual Staining <mixlab@siat.ac.cn>", 'utf-8')
        message['To'] = Header(to_email)
        message['Subject'] = Header('PGVMS verification code', 'utf-8')
        
        # ===================== 正确连接方式 =====================
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, [to_email], message.as_string())            
        logger.info(f"✅ 邮件发送成功！{code} ==> {to_email}")
        return True
    except Exception as e:
        logger.error(f"发送邮件失败: {e}")
        return False

def generate_verification_code() -> str:
    """生成6位数字验证码"""
    import random
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

@app.post("/api/send-verification-code")
async def send_verification_code(email: str):
    """发送邮箱验证码"""
    # 验证邮箱格式
    import re
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return JSONResponse({"status": "error", "message": "Invalid email format"}, status_code=400)
    
    # # 检查是否是学术邮箱，前端检查就够了，后端不检查
    # if not ('.edu' in email.lower() or '.ac' in email.lower()):
    #     return JSONResponse({"status": "error", "message": "Please use an academic email"}, status_code=400)
    
    # 生成验证码
    code = generate_verification_code()
    
    # 保存验证码（有效期5分钟）
    email_verification_codes[email] = {
        'code': code,
        'timestamp': time.time()
    }
    
    # 发送邮件
    if send_email(email, code):
        return JSONResponse({"status": "success", "message": "Verification code sent successfully"})
    else:
        # 发送失败时删除验证码记录
        del email_verification_codes[email]
        return JSONResponse({"status": "error", "message": "Failed to send verification code"}, status_code=500)

@app.post("/api/verify-code")
async def verify_code(email: str, code: str):
    """验证邮箱验证码"""
    # 检查验证码是否存在
    if email not in email_verification_codes:
        return JSONResponse({"status": "error", "message": "Verification code not found or expired"}, status_code=400)
    
    # 检查验证码是否过期（5分钟）
    record = email_verification_codes[email]
    if time.time() - record['timestamp'] > 300:
        del email_verification_codes[email]
        return JSONResponse({"status": "error", "message": "Verification code expired"}, status_code=400)
    
    # 检查验证码是否正确
    if record['code'] != code:
        return JSONResponse({"status": "error", "message": "Invalid verification code"}, status_code=400)
    
    # 验证成功，删除验证码记录
    del email_verification_codes[email]
    return JSONResponse({"status": "success", "message": "Verification successful"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8026)
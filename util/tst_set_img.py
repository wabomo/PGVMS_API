import os
from PIL import Image

def split_image(input_image_path, output_root="split_output"):
    """
    将一张大图分割成 512x512、1024x1024、2048x2048 三种尺寸的小图
    """
    # 读取大图
    img = Image.open(input_image_path)
    width, height = img.size
    print(f"原图尺寸: {width}x{height}")

    # 需要分割的尺寸
    target_sizes = [512, 1024, 2048]

    for size in target_sizes:
        # 创建输出文件夹
        out_dir = os.path.join(output_root, f"{size}x{size}")
        os.makedirs(out_dir, exist_ok=True)

        # 计算行列数（从左到右，从上到下）
        cols = width // size
        rows = height // size

        print(f"\n开始切割 {size}x{size} 小图：")
        print(f"列数: {cols} | 行数: {rows} | 总计: {cols*rows} 张")

        # 逐行逐列切割
        for row in range(rows):
            for col in range(cols):
                # 计算小图坐标
                left = col * size
                upper = row * size
                right = left + size
                lower = upper + size

                # 裁剪
                crop_img = img.crop((left, upper, right, lower))

                # 保存：行_列.png
                save_path = os.path.join(out_dir, f"{size}x{size}_row{row}_col{col}.png")
                crop_img.save(save_path)

        print(f"{size}x{size} 切割完成 ✅")

    print("\n🎉 全部尺寸切割完成！")
    print(f"文件保存在: {os.path.abspath(output_root)}")
def split_image2(input_image_path, output_root="split_output",target_sizes = [(200, 512), (1102, 1024), (2042, 2050)]):
    """
    将一张大图分割成 512x512、1024x1024、2048x2048 三种尺寸的小图
    """
    # 读取大图
    img = Image.open(input_image_path)
    width, height = img.size
    print(f"原图尺寸: {width}x{height}")

    # 需要分割的尺寸
    

    for s_w,s_h in target_sizes:
        # 创建输出文件夹
        out_dir = os.path.join(output_root, f"{s_w}x{s_h}")
        os.makedirs(out_dir, exist_ok=True)

        # 计算行列数（从左到右，从上到下）
        cols = width // s_w
        rows = height // s_h

        print(f"\n开始切割 {s_w}x{s_h} 小图：")
        print(f"列数: {cols} | 行数: {rows} | 总计: {cols*rows} 张")

        # 逐行逐列切割
        for row in range(rows):
            for col in range(cols):
                # 计算小图坐标
                left = col * s_w
                upper = row * s_h
                right = left + s_w
                lower = upper + s_h

                # 裁剪
                crop_img = img.crop((left, upper, right, lower))

                # 保存：行_列.png
                save_path = os.path.join(out_dir, f"{s_w}x{s_h}_row{row}_col{col}.png")
                crop_img.save(save_path)

        print(f"{s_w}x{s_h} 切割完成 ✅")

    print("\n🎉 全部尺寸切割完成！")
    print(f"文件保存在: {os.path.abspath(output_root)}")

# ====================== 使用 ======================
if __name__ == "__main__":
    # 把这里改成你的图片路径
    INPUT_IMAGE = "D:/siat/PGVMS/web_SHOW/001.png"
    
    # 开始分割
    # split_image(INPUT_IMAGE)
    split_image2(INPUT_IMAGE,target_sizes = [(303, 811), (401, 599), ( 1531,767), (2401, 2512)])
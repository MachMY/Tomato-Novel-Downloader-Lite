import time
import requests
import bs4
import re
import os
import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import sys
from collections import OrderedDict

# 全局配置
CONFIG = {
    "max_workers": 5,
    "max_retries": 3,
    "request_timeout": 15,
    "status_file": ".dl_status.json",
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ]
}

def get_headers(cookie=None):
    """生成随机请求头"""
    return {
        "User-Agent": random.choice(CONFIG["user_agents"]),
        "Cookie": cookie if cookie else get_cookie()
    }

def get_cookie():
    """智能Cookie管理"""
    cookie_path = "cookie.json"
    if os.path.exists(cookie_path):
        try:
            with open(cookie_path, 'r') as f:
                return json.load(f)
        except:
            pass
    
    # 生成新Cookie
    for _ in range(10):
        novel_web_id = random.randint(10**18, 10**19-1)
        cookie = f'novel_web_id={novel_web_id}'
        try:
            resp = requests.get(
                'https://fanqienovel.com',
                headers={"User-Agent": random.choice(CONFIG["user_agents"])},
                cookies={"novel_web_id": str(novel_web_id)},
                timeout=10
            )
            if resp.ok:
                with open(cookie_path, 'w') as f:
                    json.dump(cookie, f)
                return cookie
        except Exception as e:
            print(f"Cookie生成失败: {str(e)}")
            time.sleep(0.5)
    raise Exception("无法获取有效Cookie")

class NovelDownloader:
    def __init__(self, book_id, base_dir):
        self.book_id = book_id
        self.base_dir = os.path.abspath(base_dir)
        self.book_info = None
        self.save_dir = None
        self.status_file = None
        self.downloaded = set()
        self._init_book_info()
        self.status_file = os.path.join(self.save_dir, CONFIG["status_file"])
        self.downloaded = self._load_status()

    def _init_book_info(self):
        """初始化书籍元数据并创建目录"""
        for _ in range(CONFIG["max_retries"]):
            try:
                resp = requests.get(
                    f'https://fanqienovel.com/page/{self.book_id}',
                    headers=get_headers(),
                    timeout=CONFIG["request_timeout"]
                )
                if resp.status_code == 404:
                    raise Exception("小说ID不存在")
                    
                soup = bs4.BeautifulSoup(resp.text, 'lxml')
                
                # 提取元数据
                title = soup.find('h1').get_text(strip=True)
                author = self._parse_author(soup)
                desc = self._parse_description(soup)
                
                # 清理特殊字符并生成目录名
                clean_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:50]
                self.save_dir = os.path.join(self.base_dir, clean_title)
                os.makedirs(self.save_dir, exist_ok=True)
                
                self.book_info = {
                    "title": title,
                    "clean_title": clean_title,
                    "author": author,
                    "desc": desc,
                    "chapters": self._parse_chapters(soup)
                }
                return
                
            except Exception as e:
                print(f"元数据获取失败: {str(e)}")
                time.sleep(2)
        raise Exception("无法获取书籍信息")

    def _parse_author(self, soup):
        """解析作者信息"""
        author_div = soup.find('div', class_='author-name')
        return author_div.find('span', class_='author-name-text').text if author_div else "未知作者"

    def _parse_description(self, soup):
        """解析书籍简介"""
        desc_div = soup.find('div', class_='page-abstract-content')
        return desc_div.find('p').text if desc_div else "暂无简介"

    def _parse_chapters(self, soup):
        """解析章节列表"""
        chapters = []
        for idx, item in enumerate(soup.select('div.chapter-item')):
            a_tag = item.find('a')
            if not a_tag:
                continue
            
            raw_title = a_tag.get_text(strip=True)
            if re.match(r'^(番外|特别篇|if线)\s*', raw_title):
                final_title = raw_title
            else:
                clean_title = re.sub(r'^第[一二三四五六七八九十百千\d]+章\s*', '', raw_title).strip()
                final_title = f"第{idx+1}章 {clean_title}"
            
            chapters.append({
                "id": a_tag['href'].split('/')[-1],
                "title": final_title,
                "url": f"https://fanqienovel.com{a_tag['href']}",
                "index": idx
            })
        return chapters

    def _load_status(self):
        """加载下载状态"""
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, 'r') as f:
                    return set(json.load(f))
            except:
                pass
        return set()

    def _save_status(self):
        """保存下载状态"""
        with open(self.status_file, 'w') as f:
            json.dump(list(self.downloaded), f)

    def _download_chapter(self, chapter):
        """下载单个章节"""
        for retry in range(CONFIG["max_retries"]):
            try:
                api_url = f"http://fan.jingluo.love/content?item_id={chapter['id']}"
                resp = requests.get(api_url, headers=get_headers(), timeout=CONFIG["request_timeout"])
                data = resp.json()
                if data.get("code") == 0:
                    return (chapter['index'], self._clean_content(data["data"]["content"]))
                return (chapter['index'], None)
            except Exception as e:
                print(f"章节 [{chapter['title']}] 下载失败: {str(e)}")
                time.sleep(1 * (retry + 1))
        return (chapter['index'], None)

    @staticmethod
    def _clean_content(raw_html):
        """内容净化"""
        text = re.sub(r'<header>.*?</header>', '', raw_html, flags=re.DOTALL)
        text = re.sub(r'<footer>.*?</footer>', '', text, flags=re.DOTALL)
        text = re.sub(r'</?article>', '', text)
        text = re.sub(r'<p\s+idx="\d+">', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _write_to_file(self, chapters_content):
        """按顺序写入文件"""
        file_path = os.path.join(self.save_dir, f"{self.book_info['clean_title']}.txt")
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"书名：{self.book_info['title']}\n")
                f.write(f"作者：{self.book_info['author']}\n")
                f.write("简介：\n" + '\n'.join(['    ' + line for line in self.book_info['desc'].split('\n')]) + '\n\n')

        sorted_chapters = sorted(chapters_content.items(), key=lambda x: x[0])
        with open(file_path, 'a', encoding='utf-8') as f:
            for index, (chapter, content) in sorted_chapters:
                f.write(f"{chapter['title']}\n")
                f.write('\n'.join(['    ' + line for line in content.split('\n')]) + '\n\n')

    def run(self):
        """执行下载任务"""
        todo_chapters = [ch for ch in self.book_info["chapters"] if ch["id"] not in self.downloaded]
        if not todo_chapters:
            return

        content_cache = OrderedDict()
        with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as executor:
            futures = {executor.submit(self._download_chapter, ch): ch for ch in todo_chapters}
            
            with tqdm(total=len(todo_chapters), desc=self.book_info['clean_title'][:20], position=1, leave=False) as pbar:
                for future in as_completed(futures):
                    chapter = futures[future]
                    try:
                        index, content = future.result()
                        if content:
                            content_cache[index] = (chapter, content)
                            pbar.update(1)
                    except:
                        pass
                    finally:
                        pbar.refresh()

        if content_cache:
            self._write_to_file(content_cache)
            self._save_status()

def _process_single_book(book_id, base_dir):
    """处理单本小说"""
    try:
        downloader = NovelDownloader(book_id, base_dir)
        downloader.run()
        return True
    except Exception as e:
        print(f"\n小说 {book_id} 处理失败: {str(e)}")
        return False

def main():
    print("""番茄小说下载器精简版-微优化版1.2
作者：Dlmos（Dlmily）
改动者：Mach ft. DeepSeek
Github：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite
参考代码：https://github.com/ying-ck/fanqienovel-downloader/blob/main/src/ref_main.py
------------------------------------------""")
    
    # 模式选择
    mode = input("请选择模式：\n1. 单本下载/更新\n2. 批量下载/更新\n请输入数字(1/2)：").strip()
    
    if mode == '1':
        book_id = input("请输入小说ID：").strip()
        save_dir = input("保存路径（留空为当前目录）：").strip() or os.getcwd()
        _process_single_book(book_id, save_dir)
        
    elif mode == '2':
        batch_file = "book_list.txt"
        base_dir = input("请输入批量保存根目录（留空为当前目录）：").strip() or os.getcwd()
        
        if not os.path.exists(batch_file):
            print(f"检测到首次使用批量功能，正在创建引导文件 {batch_file}...")
            try:
                with open(batch_file, 'w', encoding='utf-8') as f:
                    f.write("# 请在此文件每行输入一个小说ID（记得删除汉字注解）\n")
                    f.write("# 示例：\n")
                    f.write("7182533316122000935\n")
                    f.write("7182000000000000001\n")
                    f.write("7182533316122000935\n")  # 默认示例ID
                print(f"文件 {batch_file} 已创建，请按以下步骤操作：")
                print("1. 用文本编辑器打开此文件")
                print("2. 删除示例行，填入您要下载的小说ID")
                print("3. 保存文件后重新运行本程序选择批量模式")
                sys.exit(0)
            except Exception as e:
                print(f"创建引导文件失败: {str(e)}")
                sys.exit(1)
        else:
            print(f"检测到已有 {batch_file}，请确保文件内容包含需要下载或更新的小说ID")
            confirm = input("是否立即开始批量下载？(y/n)：").strip().lower()
            if confirm != 'y':
                print("已取消批量下载")
                sys.exit(0)

        with open(batch_file, 'r', encoding='utf-8') as f:
            book_ids = [line.strip() for line in f if line.strip() and not line.startswith('#')]

        with tqdm(total=len(book_ids), desc="批量进度", position=0) as main_pbar:
            success_count = 0
            for bid in book_ids:
                try:
                    if _process_single_book(bid, base_dir):
                        success_count += 1
                except:
                    pass
                finally:
                    main_pbar.update(1)
                    main_pbar.set_postfix({"最新处理": bid})
            print(f"\n批量完成！成功：{success_count} 失败：{len(book_ids)-success_count}")
        
    else:
        print("无效输入")

if __name__ == "__main__":
    main()

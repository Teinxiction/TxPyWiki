from flask import Flask, request, render_template_string, redirect, url_for, session, make_response, jsonify
import re
import html
import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
from werkzeug.utils import secure_filename
import urllib.parse

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

BASE_DIR = "wiki_data"
PAGES_DIR = os.path.join(BASE_DIR, "pages")
FILES_DIR = os.path.join(BASE_DIR, "files")
DB_PATH = os.path.join(BASE_DIR, "wiki.db")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.3p")

for dir_path in [BASE_DIR, PAGES_DIR, FILES_DIR]:
    os.makedirs(dir_path, exist_ok=True)

DEFAULT_SETTINGS = {
    "wiki_name": "TxPyWiki",
    "wiki_icon": "/static/favicon.ico",
    "max_file_size": 5242880,
    "max_files_per_user": 10,
    "max_total_files": 1000,
    "max_total_size": 1073741824,
    "allow_anonymous_edit": False,
    "allow_registration": True,
    "default_protection": "everyone",
    "site_description": "A TxPyWiki powered wiki"
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  email TEXT,
                  is_admin INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS pages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT UNIQUE NOT NULL,
                  content TEXT,
                  protection_level TEXT DEFAULT 'everyone',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  created_by INTEGER,
                  updated_by INTEGER,
                  FOREIGN KEY(created_by) REFERENCES users(id),
                  FOREIGN KEY(updated_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  filename TEXT NOT NULL,
                  original_name TEXT NOT NULL,
                  filepath TEXT NOT NULL,
                  size INTEGER NOT NULL,
                  uploaded_by INTEGER,
                  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(uploaded_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS page_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  page_id INTEGER NOT NULL,
                  content TEXT NOT NULL,
                  edited_by INTEGER,
                  edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(page_id) REFERENCES pages(id),
                  FOREIGN KEY(edited_by) REFERENCES users(id))''')
    
    conn.commit()
    conn.close()

init_db()

def get_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                return {**DEFAULT_SETTINGS, **json.loads(content)}
        except:
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(username, password, is_admin=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                  (username, hash_password(password), 1 if is_admin else 0))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_page(title):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE title = ?", (title,))
    page = c.fetchone()
    conn.close()
    return page

def get_page_by_id(page_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
    page = c.fetchone()
    conn.close()
    return page

def create_page(title, content, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        settings = get_settings()
        c.execute('''INSERT INTO pages 
                     (title, content, protection_level, created_by, updated_by) 
                     VALUES (?, ?, ?, ?, ?)''',
                  (title, content, settings['default_protection'], user_id, user_id))
        page_id = c.lastrowid
        c.execute("INSERT INTO page_history (page_id, content, edited_by) VALUES (?, ?, ?)",
                  (page_id, content, user_id))
        conn.commit()
        return page_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def update_page(title, content, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM pages WHERE title = ?", (title,))
    page = c.fetchone()
    if page:
        page_id = page[0]
        c.execute('''UPDATE pages SET content = ?, updated_at = CURRENT_TIMESTAMP, 
                     updated_by = ? WHERE id = ?''',
                  (content, user_id, page_id))
        c.execute("INSERT INTO page_history (page_id, content, edited_by) VALUES (?, ?, ?)",
                  (page_id, content, user_id))
        conn.commit()
        return True
    return False

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM pages")
    page_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM users")
    user_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM page_history")
    edit_count = c.fetchone()[0]
    
    c.execute("SELECT MIN(created_at) FROM pages")
    first_page = c.fetchone()[0]
    
    conn.close()
    
    return {
        "pages": page_count,
        "users": user_count,
        "edits": edit_count,
        "first_edit": first_page
    }

def can_edit_page(page_title, user=None):
    page = get_page(page_title)
    if not page:
        return True
    
    protection = page[3]
    if protection == 'everyone':
        return True
    elif protection == 'loggedin':
        return user is not None
    elif protection == 'admin':
        return user and user[4] == 1
    return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        user = get_user(session.get('username'))
        if not user or user[4] != 1:
            return "需要管理员权限", 403
        return f(*args, **kwargs)
    return decorated_function

class TxPyWikiParser:
    def __init__(self):
        self.current_page = ""
        self.templates = self.load_templates()
    
    def load_templates(self):
        templates = {}
        if os.path.exists(PAGES_DIR):
            for filename in os.listdir(PAGES_DIR):
                if filename.startswith("TEMPLATE."):
                    template_name = filename[9:]
                    if '.' in template_name:
                        template_name = template_name.split('.')[0]
                    try:
                        with open(os.path.join(PAGES_DIR, filename), 'r', encoding='utf-8') as f:
                            templates[template_name] = f.read()
                    except:
                        pass
        return templates
    
    def parse_headers(self, line):
        if line.startswith('+'):
            level = line.count('+')
            text = line.lstrip('+').strip()
            if 1 <= level <= 4:
                return f'<h{level}>{self.parse_inline(text)}</h{level}>'
        return None
    
    def parse_inline(self, text):
        # 首先处理<plantext>标签，内部内容不解析
        def plantext_repl(match):
            content = match.group(1)
            # 只进行HTML转义，不进行任何其他解析
            return html.escape(content)
        
        # 先提取所有<plantext>标签的内容
        plantext_pattern = r'<plantext>(.*?)</plantext>'
        plantext_matches = list(re.finditer(plantext_pattern, text, re.DOTALL))
        
        # 如果有<plantext>标签，先替换为占位符
        if plantext_matches:
            plantext_contents = []
            for i, match in enumerate(plantext_matches):
                plantext_contents.append(match.group(1))
                text = text.replace(match.group(0), f'__PLANTEXT_{i}__', 1)
        
        # 正常的解析处理
        text = text.replace('<br>', '<br>')
        text = re.sub(r'<small>(.*?)</small>', r'<small>\1</small>', text)
        text = re.sub(r'<big>(.*?)</big>', r'<big>\1</big>', text)
        
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        
        # 处理重定向 [[[RD 目标页面]]]
        def redirect_repl(match):
            target = match.group(1).strip()
            return f'<script>window.location.href = "/wiki/{target}";</script><p>正在重定向到: <a href="/wiki/{target}">{target}</a></p>'
        text = re.sub(r'\[\[\[RD\s+(.+?)\]\]\]', redirect_repl, text)
        
        def page_link_repl(match):
            page = match.group(1)
            if '\\\\' in page:  # 表格中使用\\分隔
                page, display = page.split('\\\\', 1)
                return f'<a href="/wiki/{page}">{display}</a>'
            elif '\\' in page:  # 正常链接中使用\分隔
                page, display = page.split('\\', 1)
                return f'<a href="/wiki/{page}">{display}</a>'
            return f'<a href="/wiki/{page}">{page}</a>'
        text = re.sub(r'\(([^)]+)\)', page_link_repl, text)
        
        def github_link_repl(match):
            content = match.group(1)
            if '\\\\' in content:  # 表格中使用\\分隔
                repo, display = content.split('\\\\', 1)
                return f'<a href="https://github.com/{repo}" target="_blank">{display}</a>'
            elif '\\' in content:  # 正常链接中使用\分隔
                repo, display = content.split('\\', 1)
                return f'<a href="https://github.com/{repo}" target="_blank">{display}</a>'
            return f'<a href="https://github.com/{content}" target="_blank">{content}</a>'
        text = re.sub(r'\(github:([^)]+)\)', github_link_repl, text)
        
        def ghp_repl(match):
            try:
                user, page = match.group(1).split(':')
                return f'''
                <div class="ghp-container">
                    <iframe src="https://{user}.github.io/{page}" 
                            width="100%" 
                            height="600"
                            frameborder="0"
                            allowfullscreen></iframe>
                </div>
                '''
            except:
                return f'[GitHub Pages嵌入错误: {match.group(1)}]'
        text = re.sub(r'\(ghp:([^)]+)\)', ghp_repl, text)
        
        def ext_link_repl(match):
            content = match.group(1)
            if '\\\\' in content:  # 表格中使用\\分隔
                url, display = content.split('\\\\', 1)
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{display}</a>'
            elif '\\' in content:  # 正常链接中使用\分隔
                url, display = content.split('\\', 1)
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{display}</a>'
            else:
                if not content.startswith(('http://', 'https://')):
                    content = 'https://' + content
                return f'<a href="{content}" target="_blank" rel="noopener noreferrer">{content}</a>'
        text = re.sub(r'\{\{([^}]+)\}\}', ext_link_repl, text)
        
        text = re.sub(r'<up>(.*?)</up>', r'<sup>\1</sup>', text)
        text = re.sub(r'<dn>(.*?)</dn>', r'<sub>\1</sub>', text)
        
        text = text.replace('<pagename>', self.current_page)
        text = text.replace('<time>', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # 恢复<plantext>内容（只进行HTML转义）
        if plantext_matches:
            for i, content in enumerate(plantext_contents):
                escaped_content = html.escape(content)
                text = text.replace(f'__PLANTEXT_{i}__', escaped_content)
        
        return text
    
    def parse_template(self, lines, start_idx):
        line = lines[start_idx]
        if line.startswith('[') and not line.startswith('[['):
            template_end = start_idx
            for i in range(start_idx, len(lines)):
                if lines[i].strip() == ']':
                    template_end = i
                    break
            
            template_lines = lines[start_idx:template_end + 1]
            template_content = '\n'.join(template_lines)
            
            match = re.match(r'\[(\w+)(.*?)\]', template_content, re.DOTALL)
            if match:
                template_name = match.group(1)
                params_text = match.group(2).strip()
                
                params = {}
                lines_params = params_text.split('\n')
                for param_line in lines_params:
                    if '=' in param_line:
                        key, value = param_line.split('=', 1)
                        params[key.strip()] = value.strip()
                    elif param_line and '\\\\' in param_line:
                        params['_table_data'] = params.get('_table_data', [])
                        params['_table_data'].append(param_line.split('\\\\'))
                
                if template_name == 'table':
                    return self.generate_table(params), template_end
                elif template_name == 'navbox':
                    return self.generate_navbox(params), template_end
                elif template_name == 'file':
                    return self.handle_file(params), template_end
                elif template_name in self.templates:
                    return self.process_custom_template(template_name, params), template_end
            
            return template_content, template_end
        
        return None, start_idx
    
    def handle_file(self, params):
        if 'name' in params:
            filename = params['name']
            return f'<a href="/wiki/files/{filename}" class="file-link">{filename}</a>'
        return '[文件参数缺失]'
    
    def generate_table(self, params):
        if '_table_data' not in params:
            return ''
        
        data = params['_table_data']
        if not data:
            return ''
        
        html_output = '<div class="wiki-table">'
        if 'name' in params:
            html_output += f'<h4>{html.escape(params["name"])}</h4>'
        
        html_output += '<table>'
        
        headers = data[0]
        html_output += '<thead><tr>'
        for header in headers:
            html_output += f'<th>{html.escape(header)}</th>'
        html_output += '</tr></thead>'
        
        html_output += '<tbody>'
        for row in data[1:]:
            html_output += '<tr>'
            for cell in row:
                html_output += f'<td>{self.parse_inline(html.escape(cell))}</td>'
            html_output += '</tr>'
        html_output += '</tbody>'
        
        html_output += '</table></div>'
        return html_output
    
    def generate_navbox(self, params):
        html_output = f'''
        <div class="navbox">
            <div class="navbox-title" style="background:{params.get('color', '#cfe3ff')}">
                <span>{params.get('name', '导航')}</span>
            </div>
        '''
        
        for i in range(1, 11):
            group_key = f'g{i}'
            list_key = f'l{i}'
            
            if group_key in params:
                html_output += f'''
                <div class="navbox-group">
                    <div class="navbox-group-title" style="background:{params.get('color2', '#e8f2ff')}">
                        {params[group_key]}
                    </div>
                    <div class="navbox-content">
                        {self.parse_inline(params.get(list_key, ""))}
                '''
                
                for j in range(1, 3):
                    sub_group_key = f'g{i}.{j}'
                    sub_list_key = f'l{i}.{j}'
                    
                    if sub_group_key in params:
                        html_output += f'''
                        <div class="navbox-subgroup">
                            <div class="navbox-subgroup-title">{params[sub_group_key]}</div>
                            <div class="navbox-subgroup-content">
                                {self.parse_inline(params.get(sub_list_key, ""))}
                            </div>
                        </div>
                        '''
                
                html_output += '</div></div>'
        
        html_output += '</div>'
        return html_output
    
    def process_custom_template(self, template_name, params):
        if template_name not in self.templates:
            return f'[模板 {template_name} 未找到]'
        
        template_content = self.templates[template_name]
        
        for key, value in params.items():
            template_content = template_content.replace(f'<;{key};>', value)
        
        for i in range(1, 10):
            if f'<;{i};>' in template_content:
                template_content = template_content.replace(f'<;{i};>', params.get(str(i), ''))
        
        return self.parse_to_html(template_content, self.current_page)
    
    def parse_special_tags(self, text):
        def style_repl(match):
            return f'<style>{match.group(1)}</style>'
        text = re.sub(r'<style>(.*?)</style>', style_repl, text, flags=re.DOTALL)
        
        def script_repl(match):
            script_content = match.group(1)
            return f'''
            <div class="script-container">
                <button class="script-run-btn" onclick="runScript(this)">运行脚本</button>
                <div class="script-output" style="display:none;">
                    <iframe sandbox="allow-scripts" 
                            srcdoc="<!DOCTYPE html><html><head><script>{html.escape(script_content)}</script></head><body></body></html>"
                            width="100%" 
                            height="200"></iframe>
                </div>
            </div>
            '''
        text = re.sub(r'<script>(.*?)</script>', script_repl, text, flags=re.DOTALL)
        
        def iframe_repl(match):
            src = match.group(1)
            return f'''
            <div class="iframe-container">
                <iframe src="{src}" 
                        width="100%" 
                        height="500"
                        frameborder="0"
                        allowfullscreen></iframe>
            </div>
            '''
        text = re.sub(r'<iframe src="([^"]+)">.*?</iframe>', iframe_repl, text, flags=re.DOTALL)
        
        def img_repl(match):
            src = match.group(1)
            return f'<img src="{src}" class="wiki-image">'
        text = re.sub(r'<img src="([^"]+)">.*?</img>', img_repl, text, flags=re.DOTALL)
        
        def button_repl(match):
            button_text = match.group(3)
            attrs = match.group(1) or ''
            touch_event = match.group(2) or ''
            
            style_match = re.search(r'style="([^"]*)"', attrs)
            style = style_match.group(1) if style_match else ''
            
            button_id = f'btn_{hash(button_text + touch_event) % 10000}'
            return f'''
            <button id="{button_id}" class="wiki-button" style="{style}">{button_text}</button>
            <script>
                document.getElementById('{button_id}').addEventListener('click', function() {{
                    var output = document.createElement('div');
                    output.className = 'button-output';
                    output.innerHTML = `{self.parse_inline(touch_event)}`;
                    this.parentNode.insertBefore(output, this.nextSibling);
                }});
            </script>
            '''
        text = re.sub(r'<button(.*?)\s*"touchEvent"="([^"]*)"[^>]*>(.*?)</button>', button_repl, text)
        
        def code_repl(match):
            lang = match.group(1) or ''
            code_content = match.group(2)
            escaped_code = html.escape(code_content)
            return f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
        text = re.sub(r'<code lang="([^"]*)">(.*?)</code>', code_repl, text, flags=re.DOTALL)
        
        def co_repl(match):
            content = match.group(1)
            co_id = f'co_{hash(content) % 10000}'
            return f'''
            <div class="collapsible">
                <button class="collapsible-btn" onclick="toggleCollapse('{co_id}')">显示/隐藏内容</button>
                <div id="{co_id}" class="collapsible-content">
                    {self.parse_inline(content)}
                </div>
            </div>
            '''
        text = re.sub(r'<co>(.*?)</co>', co_repl, text, flags=re.DOTALL)
        
        def mw_repl(match):
            mw_content = match.group(1)
            return f'<div class="mw-content">{html.escape(mw_content)}</div>'
        text = re.sub(r'<mw>(.*?)</mw>', mw_repl, text, flags=re.DOTALL)
        
        text = re.sub(r'<doc>.*?</doc>', '', text, flags=re.DOTALL)
        
        return text
    
    def parse_to_html(self, text, page_name=""):
        self.current_page = page_name
        self.templates = self.load_templates()
        
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        
        lines = text.split('\n')
        html_output = []
        i = 0
        
        while i < len(lines):
            line = lines[i].rstrip()
            
            if not line.strip():
                html_output.append('')
                i += 1
                continue
            
            header = self.parse_headers(line)
            if header:
                html_output.append(header)
                i += 1
                continue
            
            template_result, new_i = self.parse_template(lines, i)
            if template_result is not None:
                html_output.append(template_result)
                i = new_i + 1
                continue
            
            parsed_line = self.parse_inline(line)
            html_output.append(f'<p>{parsed_line}</p>')
            i += 1
        
        full_html = '\n'.join(html_output)
        full_html = self.parse_special_tags(full_html)
        
        return full_html

parser = TxPyWikiParser()

BASE_CSS = '''
<style>
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    line-height: 1.6;
    color: #333;
    background: #f8f9fa;
}
.wiki-container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
}
.wiki-header {
    background: white;
    border-bottom: 1px solid #eaeaea;
    padding: 15px 0;
    margin-bottom: 30px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.header-content {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 20px;
}
.wiki-brand {
    display: flex;
    align-items: center;
    gap: 10px;
}
.wiki-icon {
    width: 32px;
    height: 32px;
    border-radius: 4px;
}
.wiki-title {
    font-size: 24px;
    font-weight: bold;
    color: #0366d6;
    text-decoration: none;
}
.wiki-title:hover {
    text-decoration: underline;
}
.search-box {
    flex: 1;
    max-width: 400px;
    min-width: 200px;
}
.search-box input {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 14px;
}
.search-box input:focus {
    outline: none;
    border-color: #0366d6;
    box-shadow: 0 0 0 3px rgba(3, 102, 214, 0.1);
}
.user-info {
    display: flex;
    align-items: center;
    gap: 15px;
}
.user-info a {
    color: #0366d6;
    text-decoration: none;
    font-size: 14px;
    padding: 6px 12px;
    border-radius: 4px;
}
.user-info a:hover {
    background: #f6f8fa;
    text-decoration: none;
}
.login-btn, .register-btn {
    background: #28a745;
    color: white !important;
    border: 1px solid #28a745;
}
.login-btn:hover, .register-btn:hover {
    background: #218838;
    border-color: #1e7e34;
}
.page-header {
    background: white;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.page-title {
    font-size: 32px;
    margin-bottom: 10px;
    color: #24292e;
}
.page-actions {
    display: flex;
    gap: 10px;
    margin-top: 15px;
}
.action-btn {
    padding: 8px 16px;
    border: 1px solid #ddd;
    background: white;
    color: #24292e;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    text-decoration: none;
    display: inline-block;
}
.action-btn:hover {
    background: #f6f8fa;
    border-color: #d1d5da;
}
.edit-btn {
    background: #0366d6;
    color: white !important;
    border-color: #0366d6;
}
.edit-btn:hover {
    background: #0256b8;
    border-color: #0256b8;
}
.upload-btn {
    background: #28a745;
    color: white !important;
    border-color: #28a745;
}
.upload-btn:hover {
    background: #218838;
    border-color: #1e7e34;
}
.wiki-content {
    background: white;
    border-radius: 8px;
    padding: 30px;
    margin-bottom: 30px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    min-height: 400px;
}
.wiki-footer {
    background: white;
    border-top: 1px solid #eaeaea;
    padding: 30px 0;
    margin-top: 30px;
    color: #586069;
    font-size: 14px;
}
.footer-content {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 30px;
}
.footer-section h3 {
    font-size: 16px;
    margin-bottom: 10px;
    color: #24292e;
}
.footer-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
    margin-top: 15px;
}
.stat-item {
    padding: 10px;
    background: #f6f8fa;
    border-radius: 4px;
    text-align: center;
}
.stat-value {
    font-size: 20px;
    font-weight: bold;
    color: #0366d6;
    display: block;
}
.stat-label {
    font-size: 12px;
    color: #586069;
}
.watermark {
    text-align: center;
    margin-top: 30px;
    padding-top: 20px;
    border-top: 1px solid #eaeaea;
    color: #959da5;
    font-size: 12px;
}
.form-container {
    max-width: 500px;
    margin: 50px auto;
    background: white;
    padding: 30px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
}
.form-group {
    margin-bottom: 20px;
}
.form-group label {
    display: block;
    margin-bottom: 5px;
    font-weight: 500;
    color: #24292e;
}
.form-group input {
    width: 100%;
    padding: 10px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 14px;
}
.form-group input:focus {
    outline: none;
    border-color: #0366d6;
    box-shadow: 0 0 0 3px rgba(3, 102, 214, 0.1);
}
.form-btn {
    width: 100%;
    padding: 12px;
    background: #0366d6;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 16px;
    cursor: pointer;
}
.form-btn:hover {
    background: #0256b8;
}
.error-message {
    color: #d73a49;
    background: #ffdce0;
    padding: 10px;
    border-radius: 4px;
    margin-bottom: 20px;
    border: 1px solid #d73a49;
}
.success-message {
    color: #28a745;
    background: #d4edda;
    padding: 10px;
    border-radius: 4px;
    margin-bottom: 20px;
    border: 1px solid #c3e6cb;
}
.wiki-table {
    margin: 20px 0;
    overflow-x: auto;
}
.wiki-table table {
    width: 100%;
    border-collapse: collapse;
}
.wiki-table th, .wiki-table td {
    border: 1px solid #ddd;
    padding: 10px;
    text-align: left;
}
.wiki-table th {
    background: #f6f8fa;
    font-weight: 600;
}
.navbox {
    border: 1px solid #a2a9b1;
    background: #f8f9fa;
    font-size: 88%;
    line-height: 1.4;
    margin: 20px 0;
}
.navbox-title {
    padding: 4px 8px;
    text-align: center;
    font-weight: bold;
}
.navbox-group {
    display: flex;
    border-top: 1px solid #eaeaea;
}
.navbox-group-title {
    width: 10em;
    padding: 4px 8px;
    text-align: right;
    border-right: 1px solid #a2a9b1;
    flex-shrink: 0;
}
.navbox-content {
    flex: 1;
    padding: 4px 8px;
}
.navbox-subgroup {
    padding: 4px 8px 4px 16px;
    border-top: 1px solid #eaeaea;
}
.navbox-subgroup-title {
    font-weight: bold;
    margin-bottom: 2px;
}
.ghp-container, .iframe-container {
    margin: 20px 0;
    border: 1px solid #ddd;
    border-radius: 4px;
    overflow: hidden;
}
.wiki-image {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
}
.wiki-button {
    padding: 8px 16px;
    background: #0366d6;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    margin: 10px 0;
}
.wiki-button:hover {
    background: #0256b8;
}
.script-container {
    margin: 20px 0;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 10px;
    background: #f6f8fa;
}
.script-run-btn {
    padding: 6px 12px;
    background: #28a745;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
}
.collapsible {
    margin: 20px 0;
}
.collapsible-btn {
    padding: 8px 16px;
    background: #6c757d;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    width: 100%;
    text-align: left;
}
.collapsible-content {
    padding: 10px;
    border: 1px solid #ddd;
    border-radius: 0 0 4px 4px;
    display: none;
}
.file-link {
    display: inline-block;
    padding: 8px 12px;
    background: #f6f8fa;
    border: 1px solid #ddd;
    border-radius: 4px;
    color: #0366d6;
    text-decoration: none;
}
.file-link:hover {
    background: #e1e4e8;
    text-decoration: underline;
}
.editor-container {
    background: white;
    border-radius: 8px;
    padding: 30px;
    margin: 20px 0;
}
.editor-textarea {
    width: 100%;
    min-height: 400px;
    padding: 15px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-family: monospace;
    font-size: 14px;
    line-height: 1.5;
    resize: vertical;
}
.editor-help {
    margin-top: 10px;
    font-size: 12px;
    color: #586069;
}
.template-manager {
    background: white;
    border-radius: 8px;
    padding: 30px;
    margin: 20px 0;
}
.template-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 20px;
    margin-top: 20px;
}
.template-item {
    background: #f6f8fa;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 20px;
}
.template-item h4 {
    margin: 0 0 10px 0;
    color: #0366d6;
}
.template-preview {
    background: white;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 10px;
    margin-top: 10px;
    font-size: 12px;
    max-height: 100px;
    overflow: auto;
}
@media (max-width: 768px) {
    .header-content {
        flex-direction: column;
        align-items: stretch;
    }
    .wiki-brand {
        justify-content: center;
    }
    .search-box {
        max-width: 100%;
    }
    .page-actions {
        flex-wrap: wrap;
    }
    .footer-content {
        flex-direction: column;
    }
    .navbox-group {
        flex-direction: column;
    }
    .navbox-group-title {
        width: 100%;
        text-align: left;
        border-right: none;
        border-bottom: 1px solid #a2a9b1;
    }
}
</style>
'''

BASE_JS = '''
<script>
function toggleCollapse(id) {
    var content = document.getElementById(id);
    if (content.style.display === "none" || content.style.display === "") {
        content.style.display = "block";
    } else {
        content.style.display = "none";
    }
}
function runScript(button) {
    var container = button.parentNode;
    var output = container.querySelector('.script-output');
    if (output.style.display === "none" || output.style.display === "") {
        output.style.display = "block";
    } else {
        output.style.display = "none";
    }
}
document.addEventListener('DOMContentLoaded', function() {
    var searchInput = document.querySelector('.search-box input');
    if (searchInput) {
        searchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                var query = this.value.trim();
                if (query) {
                    window.location.href = '/wiki/' + encodeURIComponent(query);
                }
            }
        });
    }
    var collapsibles = document.querySelectorAll('.collapsible-content');
    collapsibles.forEach(function(el) {
        el.style.display = 'none';
    });
});
</script>
'''

@app.route('/')
def root():
    return redirect('/wiki/HomePage')

@app.route('/wiki')
@app.route('/wiki/')
def wiki_home():
    return redirect('/wiki/HomePage')

@app.route('/wiki/<path:page_title>')
def wiki_page(page_title):
    # URL解码
    page_title = urllib.parse.unquote(page_title)
    
    settings = get_settings()
    stats = get_stats()
    
    user = None
    if 'user_id' in session:
        user = get_user(session.get('username'))
    
    page = get_page(page_title)
    if page:
        content = page[2]
    else:
        content = f"# 页面不存在\n页面 **{page_title}** 尚未创建。\n\n[点击编辑此页面](/wiki/edit/{urllib.parse.quote(page_title)})"
    
    html_content = parser.parse_to_html(content, page_title)
    
    # 准备URL编码的页面标题
    encoded_title = urllib.parse.quote(page_title)
    
    template = '''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>''' + page_title + ''' - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="search-box">
                        <input type="text" placeholder="搜索页面..." id="search-input">
                    </div>
                    <div class="user-info">
    '''
    
    if 'user_id' in session:
        template += '<span>欢迎，' + session.get('username', '') + '</span>'
    
    if user and user[4] == 1:
        template += '<a href="/wiki/settings">设置</a>'
        template += '<a href="/wiki/templates">模板管理</a>'
    
    if 'user_id' in session:
        template += '<a href="/wiki/logout">退出</a>'
    else:
        template += '<a href="/wiki/login" class="login-btn">登录</a>'
    
    if settings["allow_registration"] and 'user_id' not in session:
        template += '<a href="/wiki/register" class="register-btn">注册</a>'
    
    template += '''
                    </div>
                </div>
            </header>
            <div class="page-header">
                <h1 class="page-title">''' + page_title + '''</h1>
                <div class="page-actions">
                    <a href="/wiki/edit/''' + encoded_title + '''" class="action-btn edit-btn">编辑</a>
                    <a href="/wiki/upload" class="action-btn upload-btn">上传文件</a>
    '''
    
    if user and user[4] == 1:
        template += '<a href="/wiki/protect/' + encoded_title + '" class="action-btn">保护</a>'
        template += '<a href="/wiki/move/' + encoded_title + '" class="action-btn">移动</a>'
        template += '<a href="/wiki/delete/' + encoded_title + '" class="action-btn">删除</a>'
    
    template += '''
                </div>
            </div>
            <div class="wiki-content">
                ''' + html_content + '''
            </div>
            <footer class="wiki-footer">
                <div class="footer-content">
                    <div class="footer-section">
                        <h3>''' + settings["wiki_name"] + '''</h3>
                        <p>''' + settings["site_description"] + '''</p>
                    </div>
                    <div class="footer-section">
                        <h3>统计信息</h3>
                        <div class="footer-stats">
                            <div class="stat-item">
                                <span class="stat-value">''' + str(stats["pages"]) + '''</span>
                                <span class="stat-label">页面</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-value">''' + str(stats["users"]) + '''</span>
                                <span class="stat-label">用户</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-value">''' + str(stats["edits"]) + '''</span>
                                <span class="stat-label">编辑</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-value">''' + (stats["first_edit"][:10] if stats["first_edit"] else "N/A") + '''</span>
                                <span class="stat-label">始于</span>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="watermark">
                    Powered by TxPyWiki<br>
                    Generated by TeinxictionMC
                </div>
            </footer>
        </div>
        ''' + BASE_JS + '''
    </body>
    </html>
    '''
    
    return template

@app.route('/wiki/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = get_user(username)
        if user and user[2] == hash_password(password):
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['is_admin'] = user[4]
            return redirect(request.args.get('next') or '/wiki/HomePage')
        
        error = "用户名或密码错误"
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>登录 - TxPyWiki</title>
        ''' + BASE_CSS + '''
        </head>
        <body>
            <div class="wiki-container">
                <div class="form-container">
                    <h2>登录</h2>
                    <div class="error-message">''' + error + '''</div>
                    <form method="post">
                        <div class="form-group">
                            <label>用户名:</label>
                            <input type="text" name="username" required>
                        </div>
                        <div class="form-group">
                            <label>密码:</label>
                            <input type="password" name="password" required>
                        </div>
                        <button type="submit" class="form-btn">登录</button>
                    </form>
                    <p style="margin-top: 20px; text-align: center;">
                        还没有账户？ <a href="/wiki/register">注册</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        ''')
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>登录 - TxPyWiki</title>
    ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>登录</h2>
                <form method="post">
                    <div class="form-group">
                        <label>用户名:</label>
                        <input type="text" name="username" required>
                    </div>
                    <div class="form-group">
                        <label>密码:</label>
                        <input type="password" name="password" required>
                    </div>
                    <button type="submit" class="form-btn">登录</button>
                </form>
                <p style="margin-top: 20px; text-align: center;">
                    还没有账户？ <a href="/wiki/register">注册</a>
                </p>
            </div>
        </div>
    </body>
    </html>
    ''')

@app.route('/wiki/register', methods=['GET', 'POST'])
def register():
    settings = get_settings()
    if not settings["allow_registration"]:
        return "注册已关闭", 403
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            error = "两次输入的密码不一致"
        elif len(username) < 3:
            error = "用户名至少需要3个字符"
        elif len(password) < 6:
            error = "密码至少需要6个字符"
        elif get_user(username):
            error = "用户名已存在"
        else:
            if create_user(username, password):
                return redirect('/wiki/login')
            error = "注册失败，请稍后再试"
        
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>注册 - TxPyWiki</title>
        ''' + BASE_CSS + '''
        </head>
        <body>
            <div class="wiki-container">
                <div class="form-container">
                    <h2>注册</h2>
                    <div class="error-message">''' + error + '''</div>
                    <form method="post">
                        <div class="form-group">
                            <label>用户名:</label>
                            <input type="text" name="username" required>
                        </div>
                        <div class="form-group">
                            <label>密码:</label>
                            <input type="password" name="password" required>
                        </div>
                        <div class="form-group">
                            <label>确认密码:</label>
                            <input type="password" name="confirm_password" required>
                        </div>
                        <button type="submit" class="form-btn">注册</button>
                    </form>
                    <p style="margin-top: 20px; text-align: center;">
                        已有账户？ <a href="/wiki/login">登录</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        ''')
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>注册 - TxPyWiki</title>
    ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>注册</h2>
                <form method="post">
                    <div class="form-group">
                        <label>用户名:</label>
                        <input type="text" name="username" required>
                    </div>
                    <div class="form-group">
                        <label>密码:</label>
                        <input type="password" name="password" required>
                    </div>
                    <div class="form-group">
                        <label>确认密码:</label>
                        <input type="password" name="confirm_password" required>
                    </div>
                    <button type="submit" class="form-btn">注册</button>
                </form>
                <p style="margin-top: 20px; text-align: center;">
                    已有账户？ <a href="/wiki/login">登录</a>
                </p>
            </div>
        </div>
    </body>
    </html>
    ''')

@app.route('/wiki/logout')
def logout():
    session.clear()
    return redirect('/wiki/HomePage')

@app.route('/wiki/edit/<path:page_title>', methods=['GET', 'POST'])
def wiki_edit(page_title):
    page_title = urllib.parse.unquote(page_title)
    
    settings = get_settings()
    user = None
    if 'user_id' in session:
        user = get_user(session.get('username'))
    
    if not can_edit_page(page_title, user):
        return "您没有编辑此页面的权限", 403
    
    page = get_page(page_title)
    content = page[2] if page else ""
    
    if request.method == 'POST':
        new_content = request.form.get('content')
        user_id = session.get('user_id') if 'user_id' in session else None
        
        if page:
            update_page(page_title, new_content, user_id)
        else:
            create_page(page_title, new_content, user_id)
        
        return redirect(f'/wiki/{urllib.parse.quote(page_title)}')
    
    encoded_title = urllib.parse.quote(page_title)
    
    template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>编辑 ''' + page_title + ''' - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="user-info">
    '''
    
    if 'user_id' in session:
        template += '<span>欢迎，' + session.get('username', '') + '</span>'
    
    template += '<a href="/wiki/' + encoded_title + '">取消编辑</a>'
    
    template += '''
                    </div>
                </div>
            </header>
            <div class="editor-container">
                <h2>编辑: ''' + page_title + '''</h2>
                <form method="post">
                    <textarea name="content" class="editor-textarea">''' + html.escape(content) + '''</textarea>
                    <div class="editor-help">
                        使用TxPyWiki语法编辑。注意：表格中使用\\\\分隔列，普通链接中使用\\分隔。
                    </div>
                    <button type="submit" class="form-btn" style="margin-top: 20px;">保存页面</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return template

@app.route('/wiki/upload', methods=['GET', 'POST'])
@login_required
def upload_file():
    settings = get_settings()
    
    if request.method == 'POST':
        if 'file' not in request.files:
            return "没有选择文件", 400
        
        file = request.files['file']
        if file.filename == '':
            return "没有选择文件", 400
        
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(FILES_DIR, filename)
            
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            if file_size > settings["max_file_size"]:
                return f"文件太大，最大允许 {settings['max_file_size'] // 1024 // 1024}MB", 400
            
            file.save(filepath)
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''INSERT INTO files (filename, original_name, filepath, size, uploaded_by) 
                         VALUES (?, ?, ?, ?, ?)''',
                      (filename, file.filename, filepath, file_size, session['user_id']))
            conn.commit()
            conn.close()
            
            return redirect(f'/wiki/{session.get("username", "")}')
    
    max_size_mb = settings["max_file_size"] // 1024 // 1024
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>上传文件 - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="user-info">
                        <span>欢迎，''' + session.get('username', '') + '''</span>
                        <a href="/wiki/HomePage">返回首页</a>
                    </div>
                </div>
            </header>
            <div class="editor-container">
                <h2>上传文件</h2>
                <form method="post" enctype="multipart/form-data">
                    <div class="form-group">
                        <label>选择文件 (最大 ''' + str(max_size_mb) + '''MB):</label>
                        <input type="file" name="file" required>
                    </div>
                    <button type="submit" class="form-btn">上传</button>
                </form>
                <p style="margin-top: 20px; color: #586069;">
                    上传后可以在页面中使用 [file name=文件名] 来引用文件。
                </p>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/files/<filename>')
def serve_file(filename):
    filepath = os.path.join(FILES_DIR, filename)
    if os.path.exists(filepath):
        return app.send_static_file(filepath)
    return "文件不存在", 404

@app.route('/wiki/settings', methods=['GET', 'POST'])
@admin_required
def wiki_settings():
    settings = get_settings()
    
    if request.method == 'POST':
        new_settings = {}
        for key in DEFAULT_SETTINGS.keys():
            if key in request.form:
                value = request.form.get(key)
                if key in ['max_file_size', 'max_files_per_user', 'max_total_files', 'max_total_size']:
                    new_settings[key] = int(value)
                elif key in ['allow_anonymous_edit', 'allow_registration']:
                    new_settings[key] = value.lower() in ['true', 'yes', '1', 'on']
                else:
                    new_settings[key] = value
        
        save_settings(new_settings)
        settings = get_settings()
    
    allow_anonymous_selected = 'selected' if settings["allow_anonymous_edit"] else ''
    allow_registration_selected = 'selected' if settings["allow_registration"] else ''
    
    everyone_selected = 'selected' if settings["default_protection"] == 'everyone' else ''
    loggedin_selected = 'selected' if settings["default_protection"] == 'loggedin' else ''
    admin_selected = 'selected' if settings["default_protection"] == 'admin' else ''
    
    template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>设置 - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="user-info">
                        <span>管理员: ''' + session.get('username', '') + '''</span>
                        <a href="/wiki/HomePage">返回首页</a>
                    </div>
                </div>
            </header>
            <div class="editor-container">
                <h2>Wiki设置</h2>
                <form method="post">
                    <div class="form-group">
                        <label>Wiki名称:</label>
                        <input type="text" name="wiki_name" value="''' + settings["wiki_name"] + '''">
                    </div>
                    <div class="form-group">
                        <label>Wiki图标URL:</label>
                        <input type="text" name="wiki_icon" value="''' + settings["wiki_icon"] + '''">
                    </div>
                    <div class="form-group">
                        <label>站点描述:</label>
                        <input type="text" name="site_description" value="''' + settings["site_description"] + '''">
                    </div>
                    <div class="form-group">
                        <label>最大文件大小 (字节):</label>
                        <input type="number" name="max_file_size" value="''' + str(settings["max_file_size"]) + '''">
                    </div>
                    <div class="form-group">
                        <label>每个用户最大文件数:</label>
                        <input type="number" name="max_files_per_user" value="''' + str(settings["max_files_per_user"]) + '''">
                    </div>
                    <div class="form-group">
                        <label>允许匿名编辑:</label>
                        <select name="allow_anonymous_edit">
                            <option value="true" ''' + allow_anonymous_selected + '''>是</option>
                            <option value="false" ''' + ('' if settings["allow_anonymous_edit"] else 'selected') + '''>否</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>允许注册:</label>
                        <select name="allow_registration">
                            <option value="true" ''' + allow_registration_selected + '''>是</option>
                            <option value="false" ''' + ('' if settings["allow_registration"] else 'selected') + '''>否</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>默认保护级别:</label>
                        <select name="default_protection">
                            <option value="everyone" ''' + everyone_selected + '''>所有人都可编辑</option>
                            <option value="loggedin" ''' + loggedin_selected + '''>仅登录可编辑</option>
                            <option value="admin" ''' + admin_selected + '''>仅管理员可编辑</option>
                        </select>
                    </div>
                    <button type="submit" class="form-btn">保存设置</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return template

@app.route('/wiki/protect/<path:page_title>', methods=['GET', 'POST'])
@admin_required
def wiki_protect(page_title):
    page_title = urllib.parse.unquote(page_title)
    
    if request.method == 'POST':
        level = request.form.get('level')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE pages SET protection_level = ? WHERE title = ?", (level, page_title))
        conn.commit()
        conn.close()
        return redirect(f'/wiki/{urllib.parse.quote(page_title)}')
    
    page = get_page(page_title)
    current_level = page[3] if page else 'everyone'
    
    encoded_title = urllib.parse.quote(page_title)
    
    everyone_selected = 'selected' if current_level == 'everyone' else ''
    loggedin_selected = 'selected' if current_level == 'loggedin' else ''
    admin_selected = 'selected' if current_level == 'admin' else ''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>保护 ''' + page_title + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>保护页面: ''' + page_title + '''</h2>
                <form method="post">
                    <div class="form-group">
                        <label>保护级别:</label>
                        <select name="level">
                            <option value="everyone" ''' + everyone_selected + '''>所有人都可编辑</option>
                            <option value="loggedin" ''' + loggedin_selected + '''>仅登录用户可编辑</option>
                            <option value="admin" ''' + admin_selected + '''>仅管理员可编辑</option>
                        </select>
                    </div>
                    <button type="submit" class="form-btn">保存</button>
                    <a href="/wiki/''' + encoded_title + '''" class="action-btn" style="margin-left: 10px;">取消</a>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/move/<path:page_title>', methods=['GET', 'POST'])
@admin_required
def wiki_move(page_title):
    page_title = urllib.parse.unquote(page_title)
    
    if request.method == 'POST':
        new_title = request.form.get('new_title')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE pages SET title = ? WHERE title = ?", (new_title, page_title))
        conn.commit()
        conn.close()
        return redirect(f'/wiki/{urllib.parse.quote(new_title)}')
    
    encoded_title = urllib.parse.quote(page_title)
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>移动 ''' + page_title + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>移动页面: ''' + page_title + '''</h2>
                <form method="post">
                    <div class="form-group">
                        <label>新页面标题:</label>
                        <input type="text" name="new_title" value="''' + page_title + '''" required>
                    </div>
                    <button type="submit" class="form-btn">移动</button>
                    <a href="/wiki/''' + encoded_title + '''" class="action-btn" style="margin-left: 10px;">取消</a>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/delete/<path:page_title>', methods=['GET', 'POST'])
@admin_required
def wiki_delete(page_title):
    page_title = urllib.parse.unquote(page_title)
    
    if request.method == 'POST':
        confirm = request.form.get('confirm')
        if confirm == 'yes':
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM pages WHERE title = ?", (page_title,))
            conn.commit()
            conn.close()
            return redirect('/wiki/HomePage')
        else:
            return redirect(f'/wiki/{urllib.parse.quote(page_title)}')
    
    encoded_title = urllib.parse.quote(page_title)
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>删除 ''' + page_title + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>删除页面: ''' + page_title + '''</h2>
                <p style="color: #d73a49; margin-bottom: 20px;">
                    警告：此操作不可撤销！页面将被永久删除。
                </p>
                <form method="post">
                    <div class="form-group">
                        <label>确认删除？输入 "yes" 确认:</label>
                        <input type="text" name="confirm" required>
                    </div>
                    <button type="submit" class="form-btn" style="background: #d73a49;">删除</button>
                    <a href="/wiki/''' + encoded_title + '''" class="action-btn" style="margin-left: 10px;">取消</a>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/templates', methods=['GET', 'POST'])
@admin_required
def wiki_templates():
    settings = get_settings()
    
    templates = []
    if os.path.exists(PAGES_DIR):
        for filename in os.listdir(PAGES_DIR):
            if filename.startswith("TEMPLATE."):
                template_name = filename[9:]
                if '.' in template_name:
                    template_name = template_name.split('.')[0]
                try:
                    with open(os.path.join(PAGES_DIR, filename), 'r', encoding='utf-8') as f:
                        content = f.read()
                        templates.append({
                            'name': template_name,
                            'filename': filename,
                            'content': content[:200] + '...' if len(content) > 200 else content
                        })
                except:
                    pass
    
    if request.method == 'POST':
        action = request.form.get('action')
        template_name = request.form.get('template_name')
        
        if action == 'create':
            content = request.form.get('content')
            filename = f"TEMPLATE.{template_name}.3p"
            with open(os.path.join(PAGES_DIR, filename), 'w', encoding='utf-8') as f:
                f.write(content)
            return redirect('/wiki/templates')
        
        elif action == 'edit':
            content = request.form.get('content')
            filename = f"TEMPLATE.{template_name}.3p"
            with open(os.path.join(PAGES_DIR, filename), 'w', encoding='utf-8') as f:
                f.write(content)
            return redirect('/wiki/templates')
        
        elif action == 'delete':
            filename = f"TEMPLATE.{template_name}.3p"
            filepath = os.path.join(PAGES_DIR, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
            return redirect('/wiki/templates')
    
    template_items = ''
    for t in templates:
        template_items += '''
        <div class="template-item">
            <h4>''' + t['name'] + '''</h4>
            <div class="template-preview">''' + html.escape(t['content']) + '''</div>
            <div style="margin-top: 10px;">
                <a href="/wiki/edit_template/''' + t['name'] + '''" class="action-btn" style="margin-right: 10px;">编辑</a>
                <form method="post" style="display:inline;">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="template_name" value="''' + t['name'] + '''">
                    <button type="submit" class="action-btn" style="background: #d73a49; color: white; border: none;">删除</button>
                </form>
            </div>
        </div>
        '''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>模板管理 - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="user-info">
                        <span>管理员: ''' + session.get('username', '') + '''</span>
                        <a href="/wiki/HomePage">返回首页</a>
                    </div>
                </div>
            </header>
            <div class="template-manager">
                <h2>模板管理</h2>
                <div style="margin-bottom: 30px;">
                    <h3>创建新模板</h3>
                    <form method="post">
                        <input type="hidden" name="action" value="create">
                        <div class="form-group">
                            <label>模板名称:</label>
                            <input type="text" name="template_name" required>
                        </div>
                        <div class="form-group">
                            <label>模板内容:</label>
                            <textarea name="content" class="editor-textarea" rows="10" placeholder="使用<plantext>标签包裹示例代码"></textarea>
                        </div>
                        <button type="submit" class="form-btn">创建模板</button>
                    </form>
                </div>
                <div>
                    <h3>现有模板</h3>
                    <div class="template-list">
                        ''' + template_items + '''
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/edit_template/<template_name>', methods=['GET', 'POST'])
@admin_required
def wiki_edit_template(template_name):
    settings = get_settings()
    filename = f"TEMPLATE.{template_name}.3p"
    filepath = os.path.join(PAGES_DIR, filename)
    
    if not os.path.exists(filepath):
        return "模板不存在", 404
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if request.method == 'POST':
        new_content = request.form.get('content')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return redirect('/wiki/templates')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>编辑模板 ''' + template_name + ''' - ''' + settings["wiki_name"] + '''</title>
        ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <header class="wiki-header">
                <div class="header-content">
                    <div class="wiki-brand">
                        <img src="''' + settings["wiki_icon"] + '''" alt="图标" class="wiki-icon">
                        <a href="/wiki/HomePage" class="wiki-title">''' + settings["wiki_name"] + '''</a>
                    </div>
                    <div class="user-info">
                        <span>管理员: ''' + session.get('username', '') + '''</span>
                        <a href="/wiki/templates">返回模板管理</a>
                    </div>
                </div>
            </header>
            <div class="editor-container">
                <h2>编辑模板: ''' + template_name + '''</h2>
                <form method="post">
                    <textarea name="content" class="editor-textarea">''' + html.escape(content) + '''</textarea>
                    <div class="editor-help">
                        使用<plantext>标签包裹示例代码，如: &lt;plantext&gt;示例代码&lt;/plantext&gt;
                    </div>
                    <button type="submit" class="form-btn" style="margin-top: 20px;">保存模板</button>
                    <a href="/wiki/templates" class="action-btn" style="margin-left: 10px;">取消</a>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/wiki/api/<action>', methods=['GET', 'POST'])
def wiki_api(action):
    if action == 'get_page':
        page_title = request.args.get('page')
        page = get_page(page_title)
        if page:
            return jsonify({
                'title': page[1],
                'content': page[2],
                'protection': page[3],
                'created': page[4],
                'updated': page[5]
            })
        return jsonify({'error': '页面不存在'}), 404
    
    elif action == 'edit_page':
        page_title = request.args.get('page')
        content = request.args.get('content')
        username = request.args.get('username')
        password = request.args.get('password')
        
        user = get_user(username)
        if not user or user[2] != hash_password(password):
            return jsonify({'error': '认证失败'}), 401
        
        if not can_edit_page(page_title, user):
            return jsonify({'error': '没有编辑权限'}), 403
        
        if get_page(page_title):
            update_page(page_title, content, user[0])
        else:
            create_page(page_title, content, user[0])
        
        return jsonify({'success': True})
    
    elif action == 'create_account':
        settings = get_settings()
        if not settings["allow_registration"]:
            return jsonify({'error': '注册已关闭'}), 403
        
        username = request.args.get('username')
        password = request.args.get('password')
        
        if create_user(username, password):
            return jsonify({'success': True})
        return jsonify({'error': '用户名已存在'}), 400
    
    elif action == 'get_stats':
        stats = get_stats()
        return jsonify(stats)
    
    elif action == 'search':
        query = request.args.get('q', '')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT title FROM pages WHERE title LIKE ?", (f'%{query}%',))
        results = [row[0] for row in c.fetchall()]
        conn.close()
        return jsonify({'results': results})
    
    elif action == '' or action == 'help':
        return jsonify({
            'api_endpoints': {
                '/wiki/api/get_page?page=<title>': '获取页面内容',
                '/wiki/api/edit_page?page=<title>&content=<content>&username=<user>&password=<pass>': '编辑页面',
                '/wiki/api/create_account?username=<user>&password=<pass>': '创建账户',
                '/wiki/api/get_stats': '获取统计信息',
                '/wiki/api/search?q=<query>': '搜索页面'
            },
            'note': '所有API调用都需要相应的权限'
        })
    
    return jsonify({'error': '无效的API操作'}), 400

def check_initial_setup():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
    admin_count = c.fetchone()[0]
    conn.close()
    
    if admin_count == 0:
        if not os.path.exists(PAGES_DIR):
            os.makedirs(PAGES_DIR, exist_ok=True)
        
        homepage_content = '''+欢迎来到TxPyWiki

这是一个基于TxPyWiki的Wiki系统。

++功能特性
* 完整的TxPyWiki语法支持
* 用户系统（注册、登录）
* 页面保护（所有人都可编辑/仅登录可编辑/仅管理员可编辑）
* 文件上传
* 搜索功能
* 响应式设计

++快速开始
1. (login\\登录)或(register\\注册)账户
2. 点击页面上的"编辑"按钮开始编辑
3. 使用TxPyWiki语法编写内容

++语法示例
<plantext>
+大标题
++二级标题

文本内容<br>换行

[table
name=示例表格
名称\\\\数量
苹果\\\\10
香蕉\\\\20
]
</plantext>

++帮助
如需帮助，请查看(帮助页面)或联系管理员。
'''
        
        with open(os.path.join(PAGES_DIR, "HomePage.3p"), 'w', encoding='utf-8') as f:
            f.write(homepage_content)
        
        help_content = '''+帮助页面

++TxPyWiki语法
[table
name=基本语法
语法\\\\描述\\\\示例
+\\\\一级标题\\\\+大标题
++\\\\二级标题\\\\++二级标题
<small>\\\\小号文字\\\\<small>小文字</small>
<big>\\\\大号文字\\\\<big>大文字</big>
<br>\\\\换行\\\\文本<br>换行
]

++链接语法
[table
name=链接语法
类型\\\\语法\\\\示例
内部链接\\\\<plantext>(页面名称\\显示文本)</plantext>\\\\(首页\\返回首页)
外部链接\\\\<plantext>{{URL\\显示文本}}</plantext>\\\\{{https://example.com\\示例网站}}
]

++表格语法
<plantext>
[table
name=表格名称
列1\\\\列2\\\\列3
行1列1\\\\行1列2\\\\行1列3
行2列1\\\\行2列2\\\\行2列3
]
</plantext>

++重定向语法
使用 <plantext>[[[RD 目标页面名称]]]</plantext> 可以创建重定向页面。

++模板语法
1. 创建模板文件: TEMPLATE.模板名.3p
2. 在模板中使用 <;参数名;> 作为占位符
3. 在页面中调用: [模板名 参数=值]

++特殊标签
* `<script>` - 嵌入JavaScript代码
* `<style>` - 嵌入CSS样式
* `<iframe>` - 嵌入外部网站
* `<code>` - 代码块
* `<co>` - 可折叠内容
* `<plantext>` - 纯文本，内部标签不会被解析
'''
        
        with open(os.path.join(PAGES_DIR, "帮助页面.3p"), 'w', encoding='utf-8') as f:
            f.write(help_content)
        
        template_content = '''<div class="info-box">
    <h3><;title;></h3>
    <p><;content;></p>
    <small>创建于: <time></small>
</div>

<doc>
使用 [InfoBox
title=标题
content=内容] 来调用此模板
</doc>'''
        
        with open(os.path.join(PAGES_DIR, "TEMPLATE.InfoBox.3p"), 'w', encoding='utf-8') as f:
            f.write(template_content)
        
        save_settings(DEFAULT_SETTINGS)
        
        return True
    return False

@app.before_request
def setup_check():
    if request.path == '/wiki/setup_admin' or request.path.startswith('/static/'):
        return
    
    if check_initial_setup():
        return redirect('/wiki/setup_admin')

@app.route('/wiki/setup_admin', methods=['GET', 'POST'])
def setup_admin():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        
        if password != confirm:
            return "两次输入的密码不一致", 400
        
        if create_user(username, password, is_admin=True):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            with open(os.path.join(PAGES_DIR, "HomePage.3p"), 'r', encoding='utf-8') as f:
                content = f.read()
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            user_id = c.fetchone()[0]
            
            create_page("HomePage", content, user_id)
            create_page("帮助页面", open(os.path.join(PAGES_DIR, "帮助页面.3p"), 'r', encoding='utf-8').read(), user_id)
            
            conn.close()
            
            session['user_id'] = user_id
            session['username'] = username
            session['is_admin'] = 1
            
            return redirect('/wiki/HomePage')
        
        return "创建管理员失败", 400
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>TxPyWiki 初始化</title>
    ''' + BASE_CSS + '''
    </head>
    <body>
        <div class="wiki-container">
            <div class="form-container">
                <h2>TxPyWiki 初始化设置</h2>
                <p style="margin-bottom: 20px;">
                    欢迎使用TxPyWiki！首先需要创建一个管理员账户。
                </p>
                <form method="post">
                    <div class="form-group">
                        <label>管理员用户名:</label>
                        <input type="text" name="username" required>
                    </div>
                    <div class="form-group">
                        <label>密码:</label>
                        <input type="password" name="password" required>
                    </div>
                    <div class="form-group">
                        <label>确认密码:</label>
                        <input type="password" name="confirm_password" required>
                    </div>
                    <button type="submit" class="form-btn">创建管理员账户</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    ''')

if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        init_db()
    
    app.run(host='0.0.0.0', port=5000, debug=True)

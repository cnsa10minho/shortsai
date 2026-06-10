import os
import io
import re
import numpy as np
import json
import requests
import urllib.parse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from google import genai
from google.genai import types

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

# ─── API KEY ────────────────────────
# ─── API KEY ────────────────────────
# .env 파일에서 불러온 환경 변수만 사용하도록 변경 (기본값 삭제)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

ai_client = genai.Client(api_key=GEMINI_API_KEY)


# ════════════════════════════════════════════════════════════════════════════
#  1. 공식 사이트 맞춤형 실시간 크롤링
# ════════════════════════════════════════════════════════════════════════════
def get_realtime_center_notices(max_pages: int = 10) -> list:
    notices = []
    notice_id = 1
    BASE = "https://www.gggongik.or.kr"
    BOARD_PATH = "/page/centernews/centernotice.html"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": BASE,
    }

    print(f"\n🌐 [공식 사이트 타겟 크롤링 엔진 가동] 최대 {max_pages}페이지 분석 중...")
    session = requests.Session()
    session.headers.update(HEADERS)

    for page in range(1, max_pages + 1):
        try:
            url = f"{BASE}{BOARD_PATH}?page={page}"
            resp = session.get(url, timeout=12, verify=False)
            
            if resp.status_code != 200:
                print(f"  ❌ [{page}페이지] 서버 응답 오류 (코드: {resp.status_code})")
                continue

            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            items = soup.select(".bbs_list li") or soup.select(".board_list li") or soup.select(".list_box li")
            if not items:
                items = soup.find_all("a", href=re.compile(r"seq=|idx=|mode=view"))

            found_in_page = 0
            for item in items:
                a_tag = item if item.name == "a" else item.find("a")
                if not a_tag: continue

                href = a_tag.get("href", "")
                detail_url = href if href.startswith("http") else (BASE + href if href.startswith("/") else BASE + BOARD_PATH + href)

                title_el = a_tag.select_one(".txt_box strong") or a_tag.select_one(".title") or a_tag
                title = title_el.get_text(strip=True)

                if not title or len(title) < 6 or title.isdigit(): continue

                date_text = "홈페이지 공고 참조"
                if item.name != "a":
                    date_el = item.select_one(".date") or item.select_one(".time") or item.find(class_=re.compile(r"date|reg", re.I))
                    if date_el: date_text = date_el.get_text(strip=True)
                
                if date_text == "홈페이지 공고 참조":
                    item_text = item.get_text(" ", strip=True)
                    date_match = re.search(r"202\d[-./]\d{1,2}[-./]\d{1,2}", item_text)
                    if date_match: date_text = date_match.group()

                if any(k in title for k in ["공모", "모집", "지원", "선정", "채용", "공고"]): cat = "공모"
                elif any(k in title for k in ["행사", "교육", "데이", "워크숍", "특강", "포럼", "세미나"]): cat = "행사안내"
                else: cat = "일반공지"

                body_text = f"경기도공익활동지원센터에서 새롭게 공지한 소식입니다. '{title}'에 대한 세부 모집 요강과 지원 자격 요건 등을 공식 홈페이지 게시글을 통해 꼼꼼히 확인하시고 많은 참여 바랍니다."

                notices.append({
                    "id": notice_id, "cat": cat, "title": title, "body": body_text,
                    "deadline": date_text, "limit": "도민 및 공익활동 단체 대상", "url": detail_url
                })
                notice_id += 1
                found_in_page += 1
            print(f"  └─ {page}페이지 분석 완료: {found_in_page}건 수집")
        except Exception as e:
            print(f"  ⚠️ [{page}페이지] 크롤링 중 예외 발생: {e}")

    if not notices: return _backup_notices()
    return notices

def _backup_notices() -> list:
    return [
        {"id": 1, "cat": "공모", "title": "2026년 경기도 청년 공익활동가 역량강화 지원사업 참가자 모집", "body": "경기도 내 청년 공익활동가를 대상으로 실무 교육비 및 멘토링 프로그램을 지원합니다.", "deadline": "2026년 6월 30일", "limit": "도내 만 19세~34세 청년", "url": "https://www.gggongik.or.kr"},
        {"id": 2, "cat": "행사안내", "title": "제4회 경기 공익활동가 네트워킹 데이 참가자 모집", "body": "공익활동가들의 연대와 협업을 도모하는 네트워킹 행사입니다.", "deadline": "선착순 마감", "limit": "공익활동가 및 도민 누구나", "url": "https://www.gggongik.or.kr"}
    ]


# ════════════════════════════════════════════════════════════════════════════
#  2. Flask 라우트 (목록 조회)
# ════════════════════════════════════════════════════════════════════════════
@app.route("/api/get-real-notices", methods=["GET"])
def get_real_notices():
    pages = int(request.args.get("pages", 10))
    data = get_realtime_center_notices(max_pages=pages)
    return jsonify(data)


# ════════════════════════════════════════════════════════════════════════════
#  3. Gemini 대본 및 일러스트 최적화 검색어 생성기
# ════════════════════════════════════════════════════════════════════════════
def ask_gemini_for_script(title, category, body, deadline, limit) -> dict:
    prompt = f"""
당신은 숏폼 전문 크리에이터입니다.
아래 공지사항 데이터를 기반으로 시청자를 사로잡는 트렌디한 구어체 15초 숏폼 대본을 작성해 주세요.

각 씬의 자막 상단에 들어갈 이미지를 검색하기 위한 키워드들을 생성할 때, 아래의 규칙을 엄격하게 지켜주세요. 
전체 영상이 부드럽고 따뜻한 '플랫 일러스트' 디자인 톤으로 통일되어야 합니다.

[중요: 이미지 검색어 생성 규칙]
1. 절대로 '한국', '대한민국', '국내', '국기', '세종대왕' 같은 국가 상징 키워드를 넣지 마세요.
2. 부드럽고 감성적인 파스텔톤의 비즈니스/공공 디자인 일러스트가 검색되도록 단어를 구성하세요.
3. 검색어 구성 방식: "[핵심명사] 플랫 일러스트" 또는 "[핵심행동] 파스텔 일러스트" 형태로 생성하세요.
   - 좋은 예시: "청년 노트북 플랫 일러스트", "달력 서류 파스텔 일러스트", "사람들 화합 플랫 일러스트", "컴퓨터 마우스 클릭 일러스트"

[공지 데이터]
카테고리: {category}
제목: {title}
본문: {body}
마감: {deadline}
대상: {limit}

반드시 아래 형식을 지킨 순수 JSON 데이터만 응답하세요. 마크다운 기호(```)나 대괄호 링크, 부연설명은 절대로 금지합니다.
{{
  "bg_search_query": "파스텔 그라데이션 배경 플랫 일러스트",
  "hook_title": "첫 3초 시선 후킹 제목",
  "hook_sub": "훅 상세 대사",
  "hook_image_query": "청년 참여 파스텔 일러스트",
  "info_title": "핵심 요약 코너 제목",
  "info_sub": "요약 내용 대사",
  "info_image_query": "달력 서류 파스텔 일러스트",
  "out_title": "행동 유도 문구",
  "out_sub": "홈페이지 방문 유도 대사",
  "out_image_query": "웹사이트 클릭 플랫 일러스트"
}}
"""
    try:
        resp = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = resp.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"⚠️ Gemini 오류: {e}")
        return {
            "bg_search_query": "파스텔 그라데이션 배경",
            "hook_title": "나만 몰랐던 경기도 꿀혜택 대공개! 🔥",
            "hook_sub": "이번에 나온 소식인데 조건이 진짜 대박이야. 끝까지 봐봐!",
            "hook_image_query": "스마트폰 알림 플랫 일러스트",
            "info_title": "📋 핵심만 빠르게 요약",
            "info_sub": f"{limit} 대상이고, 마감은 {deadline}이니까 바로 신청해봐!",
            "info_image_query": "사무 서류 파스텔 일러스트",
            "out_title": "🔗 지금 바로 확인하기",
            "out_sub": "공식 홈페이지 프로필 링크에서 지금 바로 확인해봐!",
            "out_image_query": "웹사이트 클릭 일러스트"
        }


# ════════════════════════════════════════════════════════════════════════════
#  4. 일러스트 스타일 타겟 검색 및 다운로드 엔진 (마크다운 링크 완전 세척 정제 파트)
# ════════════════════════════════════════════════════════════════════════════
def search_and_download_image(query: str, target_w: int, target_h: int) -> Image.Image:
    try:
        # 1. 원본 쿼리 내 특수 기호 및 제어 문자 일차 세척
        query = "".join(ch for ch in str(query) if ch.isprintable())
        query = re.sub(r'\[|\]|\(|\)|\'|\"|\\n|\\r', '', query).strip()
        
        # 2. 만약 마크다운 서식(http가 쿼리에 강제 유출된 경우)이 있으면 뒷부분을 날리고 알맹이 검색어만 확보
        if "http" in query:
            query = query.split("http")[0].strip()
            query = re.sub(r'\[|\]|\(|\)|\:|\/', '', query).strip()

        for bad_word in ["한국", "대한민국", "국내", "미국", "국기", "태극기"]:
            query = query.replace(bad_word, "").strip()

        if not any(k in query for k in ["일러스트", "플랫", "그래픽", "드로잉"]):
            search_query = f"{query} 플랫 일러스트"
        else:
            search_query = query

        print(f"🔍 [부드러운 일러스트풍 검색] 최종 쿼리: {search_query}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9"
        }
        
        encoded_query = urllib.parse.quote(search_query)
        
        # 💡 [핵심 해결 조치] 어떠한 대괄호나 가짜 마크다운 서식도 끼지 못하도록 주소를 순수 텍스트 포맷으로 직접 재구축합니다.
        search_url = f"[https://www.bing.com/images/search?q=](https://www.bing.com/images/search?q=){encoded_query}&cc=KR&setlang=ko&first=1"
        search_url = str(search_url).replace("[", "").replace("]", "").replace("(", "").replace(")", "").strip()
        
        res = requests.get(search_url, headers=headers, timeout=10, verify=False)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        
        img_tags = soup.select("a.iusc")
        img_urls = []
        
        for tag in img_tags:
            m_attr = tag.get("m")
            if m_attr:
                try:
                    m_json = json.loads(m_attr)
                    if "murl" in m_json: img_urls.append(m_json["murl"])
                except Exception: continue

        for target_url in img_urls[:15]:
            try:
                target_url = "".join(ch for ch in str(target_url) if ch.isprintable())
                target_url = re.sub(r'\[|\]|\(|\)|\s', '', target_url).strip()
                if not target_url.startswith("http"): continue
                
                bad_keywords = [
                    ".cn/", ".jp/", "pimg", "chinabyte", "flag", "national", 
                    "wikipedia", "wikimedia", "emblem", "국기", "태극기", "photo"
                ]
                if any(bad in target_url.lower() for bad in bad_keywords):
                    continue

                img_res = requests.get(target_url, headers=headers, timeout=5, verify=False)
                img_res.raise_for_status()
                
                downloaded_img = Image.open(io.BytesIO(img_res.content))
                downloaded_img.verify()
                
                downloaded_img = Image.open(io.BytesIO(img_res.content)).convert("RGB")
                return resize_and_center_crop(downloaded_img, target_w, target_h)
            except Exception: continue
                
    except Exception as e:
        print(f"⚠️ 이미지 다운로드 실패 원인 분석: {e}")
    return None

def resize_and_center_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    orig_w, orig_h = img.size
    scale = max(target_w / orig_w, target_h / orig_h)
    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)
    
    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img_resized.crop((left, top, left + target_w, top + target_h))


# ════════════════════════════════════════════════════════════════════════════
#  5. 폴백 디자인 시스템
# ════════════════════════════════════════════════════════════════════════════
CATEGORY_PALETTES = {
    "공모":    ((224, 242, 254), (186, 230, 253), (56, 189, 248), (14, 165, 233)), 
    "행사안내": ((243, 232, 255), (233, 213, 255), (192, 132, 252), (168, 85, 247)), 
    "일반공지": ((204, 251, 241), (153, 246, 228), (45, 212, 191), (13, 148, 136)),  
    "default": ((241, 245, 249), (226, 232, 240), (148, 163, 184), (100, 116, 139)),
}

def generate_safe_graphic_background(category: str = "default") -> Image.Image:
    # 1080x1920 사이즈의 하늘색(RGB: 230, 244, 255) 이미지 생성
    W, H = 1080, 1920
    # CSS의 --sky-light 컬러값과 유사한 밝은 하늘색
    background_color = (230, 244, 255) 
    img = Image.new("RGB", (W, H), color=background_color)
    return img
    
    def _lerp_color(c1, c2, t): return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    
    for y in range(H):
        t = y / H
        col = _lerp_color(top_c, mid_c, t * 2) if t < 0.5 else _lerp_color(mid_c, bot_c, (t - 0.5) * 2)
        draw.line([(0, y), (W, y)], fill=col)

    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    
    def soft_circle(cx, cy, radius, color_rgb, max_alpha):
        for i in range(30, 0, -1):
            r = int(radius * i / 30)
            a = int(max_alpha * (i / 30) ** 2)
            coord = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
            gd.ellipse(coord, fill=(int(color_rgb[0]), int(color_rgb[1]), int(color_rgb[2]), int(a)))
            
    soft_circle(150, 300, 500, acc, 40)
    soft_circle(900, 1600, 600, (255, 255, 255), 50)
    return Image.alpha_composite(img.convert("RGBA"), glow_layer).convert("RGB")

def generate_backup_card_graphic(category: str = "default") -> Image.Image:
    w, h = 800, 420
    palette = CATEGORY_PALETTES.get(category, CATEGORY_PALETTES["default"])
    card = Image.new("RGB", (w, h), color=palette[1])
    draw = ImageDraw.Draw(card)
    
    draw.ellipse((-50, -50, 150, 150), fill=palette[2])
    draw.ellipse((w-100, h-100, w+100, h+100), fill=palette[0])
    for i in range(0, w, 60):
        draw.line([(i, 0), (i + 30, h)], fill=palette[2], width=1)
    return card


# ════════════════════════════════════════════════════════════════════════════
#  6. 씬 프레임 레이아웃 렌더러
# ════════════════════════════════════════════════════════════════════════════
def _load_korean_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "malgunbd.ttf", "malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/Library/Fonts/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    ]
    for path in candidates:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except Exception: continue
    return ImageFont.load_default()

def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width: current = test
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return lines or [text]

def create_scene_frame(title: str, subtitle: str, tag_label: str, bg_image: Image.Image, content_image: Image.Image) -> np.ndarray:
    # [수정] 배경을 하늘색 단색 (230, 244, 255)으로 고정 생성
    W, H = 1080, 1920
    background_color = (230, 244, 255)
    frame = Image.new("RGB", (W, H), color=background_color)
    
    draw = ImageDraw.Draw(frame)
    f_tag  = _load_korean_font(42)
    f_main = _load_korean_font(52)
    f_sub  = _load_korean_font(38)

    box_x0, box_y0, box_x1, box_y1 = 70, 340, 1010, 1360
    box_w = box_x1 - box_x0

    # 카드 영역 생성
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((box_x0, box_y0, box_x1, box_y1), radius=40, fill=(255, 255, 255, 245))
    overlay_draw.rounded_rectangle((box_x0-1, box_y0-1, box_x1+1, box_y1+1), radius=40, outline="#E2E8F0", width=2)
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(frame)

    # 태그 및 콘텐츠 레이아웃
    tw = draw.textlength(tag_label, font=f_tag)
    pad_x, pad_y = 26, 12
    tx0, ty0 = int(540 - tw / 2 - pad_x), 180
    tx1, ty1 = int(540 + tw / 2 + pad_x), ty0 + 42 + pad_y * 2
    draw.rounded_rectangle((tx0, ty0, tx1, ty1), radius=20, fill="#38BDF8")
    draw.text((int(540 - tw / 2), ty0 + pad_y), tag_label, fill="#FFFFFF", font=f_tag)

    current_y = box_y0 + 45
    img_w, img_h = 800, 420
    mask = Image.new("L", (img_w, img_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, img_w, img_h), radius=24, fill=255)
    
    img_x = box_x0 + (box_w - img_w) // 2
    frame.paste(content_image, (img_x, current_y), mask=mask)
    current_y += img_h + 45

    # 텍스트 렌더링
    draw = ImageDraw.Draw(frame)
    title_lines = _wrap_text(draw, title, f_main, max_width=box_w - 80)
    for line in title_lines[:2]:
        lw = draw.textlength(line, font=f_main)
        draw.text(((1080 - lw) // 2, current_y), line, fill="#1E293B", font=f_main)
        current_y += 76

    current_y += 10
    draw.line([(box_x0 + 60, current_y), (box_x1 - 60, current_y)], fill="#F1F5F9", width=2)
    current_y += 35

    sub_lines = _wrap_text(draw, subtitle, f_sub, max_width=box_w - 80)
    HIGHLIGHT_KEYWORDS = {"신청", "기한", "마감", "링크", "청년", "지원", "무료", "혜택", "공모", "참여", "확인"}
    for sl in sub_lines[:4]:
        sw = draw.textlength(sl, font=f_sub)
        color = "#0284C7" if any(k in sl for k in HIGHLIGHT_KEYWORDS) else "#475569"
        draw.text(((1080 - sw) // 2, current_y), sl, fill=color, font=f_sub)
        current_y += 62

    return np.array(frame)

# ════════════════════════════════════════════════════════════════════════════
#  7. 숏폼 영상 최종 결합 및 렌더링 컨트롤러 API
# ════════════════════════════════════════════════════════════════════════════
@app.route("/api/generate-shorts", methods=["POST"])
def generate_shorts():
    tmp_files = []
    try:
        data     = request.json or {}
        title    = data.get("title",    "")
        category = data.get("category", "공지사항")
        body     = data.get("body",     "")
        deadline = data.get("deadline", "홈페이지 참조")
        limit    = data.get("limit",    "대상 요강 참조")

        print(f"\n🚀 [숏폼 오케스트레이터 구동]: {title}")

        sc = ask_gemini_for_script(title, category, body, deadline, limit)
        
        bg_query   = sc.get("bg_search_query", "파스텔 그라데이션 배경")
        hook_query = sc.get("hook_image_query", "청년 일러스트")
        info_query = sc.get("info_image_query", "달력 서류 일러스트")
        out_query  = sc.get("out_image_query", "마우스 클릭 일러스트")

        bg_img = search_and_download_image(bg_query, 1080, 1920) or generate_safe_graphic_background(category=category)

        img_content_hook = search_and_download_image(hook_query, 800, 420) or generate_backup_card_graphic(category=category)
        img_content_info = search_and_download_image(info_query, 800, 420) or generate_backup_card_graphic(category=category)
        img_content_out  = search_and_download_image(out_query, 800, 420) or generate_backup_card_graphic(category=category)

        img_h = create_scene_frame(sc["hook_title"], sc["hook_sub"], f"🔥 {category} 소식", bg_img, img_content_hook)
        img_i = create_scene_frame(sc["info_title"], sc["info_sub"], "📌 핵심 요약 정보", bg_img, img_content_info)
        img_o = create_scene_frame(sc["out_title"],  sc["out_sub"], "🔗 참여하는 방법", bg_img, img_content_out)

        tts_paths = []
        for idx, (t, s) in enumerate([
            (sc["hook_title"], sc["hook_sub"]),
            (sc["info_title"], sc["info_sub"]),
            (sc["out_title"],  sc["out_sub"]),
        ]):
            path = f"_tts_{idx}.mp3"
            gTTS(text=f"{t}. {s}", lang="ko").save(path)
            tts_paths.append(path)
            tmp_files.append(path)

        clips = []
        for frame, mp3 in zip([img_h, img_i, img_o], tts_paths):
            aud  = AudioFileClip(mp3)
            clip = ImageClip(frame).set_duration(aud.duration).set_audio(aud)
            clips.append(clip)

        final = concatenate_videoclips(clips, method="compose")
        out_path = os.path.join(os.getcwd(), "gongik_shorts_output.mp4")

        final.write_videofile(
            out_path, fps=24,
            codec="libx264", audio_codec="aac",
            logger=None,
        )
        final.close()
        for c in clips: c.close()

        print("✅ [통합 일러스트풍 숏폼 비디오 빌드 완료]")
        return send_file(
            out_path, mimetype="video/mp4", as_attachment=True, download_name="gongik_shorts.mp4"
        )

    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return jsonify({"error": str(e)}), 500

    finally:
        for f in tmp_files:
            try: os.remove(f)
            except Exception: pass


if __name__ == "__main__":
    app.run(port=5000, debug=True)
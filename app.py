import os
from flask import Flask, render_template, jsonify, request
from neo4j import GraphDatabase
from flask_cors import CORS
import random

app = Flask(__name__)
CORS(app)

# Đọc từ biến môi trường (Environment Variables)
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "12345678")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def get_graph_data():
    nodes = []
    edges = []
    node_ids = set()

    with driver.session() as session:
        # Lấy tất cả các Nodes thuộc chủ đề hình học
        result_nodes = session.run("MATCH (n) WHERE n:TuGiac OR n:HinhHocPhang RETURN n")
        for record in result_nodes:
            node = record["n"]
            props = dict(node)
            node_id = props.get("id", str(node.element_id))
            
            if node_id not in node_ids:
                node_ids.add(node_id)
                nodes.append({
    "id": node_id,
    "label": props.get("name", node_id),
    "name": props.get("name", ""),
    "tinh_chat": props.get("tinh_chat", ""),
    "nhan_biet": props.get("nhan_biet", ""),
    "chu_vi": props.get("chu_vi", ""),
    "dien_tich": props.get("dien_tich", ""),
    "meo": props.get("meo", ""),  # <-- Thêm trường này
    "labels": list(node.labels)
})

        # Lấy tất cả các Relationships
        result_rels = session.run("""
            MATCH (n)-[r]->(m)
            WHERE (n:TuGiac OR n:HinhHocPhang) AND (m:TuGiac OR m:HinhHocPhang)
            RETURN n.id AS source, m.id AS target, type(r) AS rel_type, r.dieu_kien AS dieu_kien
        """)
        for record in result_rels:
            edges.append({
                "from": record["source"],
                "to": record["target"],
                "label": record["dieu_kien"] if record["dieu_kien"] else record["rel_type"],
                "type": record["rel_type"]
            })

    return {"nodes": nodes, "edges": edges}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/graph", methods=["GET"])
def api_graph():
    try:
        data = get_graph_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== API NGƯỜI CHƠI, ĐIỂM SỐ & XU ====================
@app.route("/api/player/get_or_create", methods=["POST"])
def get_or_create_player():
    """Tạo hoặc lấy thông tin người chơi theo Nickname"""
    data = request.json or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Tên người chơi không được để trống"}), 400

    query = """
    MERGE (p:Player {username: $username})
    ON CREATE SET p.score = 0, p.coins = 0, p.created_at = timestamp()
    RETURN p.username AS username, p.score AS score, p.coins AS coins
    """
    with driver.session() as session:
        rec = session.run(query, username=username).single()
        return jsonify({
            "username": rec["username"],
            "score": rec["score"],
            "coins": rec["coins"]
        })


@app.route("/api/quiz/finish", methods=["POST"])
def finish_quiz():
    """Cộng dồn điểm (Score) và xu (Coins) sau khi hoàn thành Quiz"""
    data = request.json or {}
    username = data.get("username")
    earned_score = int(data.get("earned_score", 0))
    earned_coins = int(data.get("earned_coins", 0))

    if not username:
        return jsonify({"error": "Thiếu username"}), 400

    query = """
    MATCH (p:Player {username: $username})
    SET p.score = p.score + $score,
        p.coins = p.coins + $coins
    RETURN p.username AS username, p.score AS score, p.coins AS coins
    """
    with driver.session() as session:
        rec = session.run(query, username=username, score=earned_score, coins=earned_coins).single()
        if not rec:
            return jsonify({"error": "Không tìm thấy người chơi"}), 404
        return jsonify({
            "username": rec["username"],
            "score": rec["score"],
            "coins": rec["coins"]
        })


@app.route("/api/leaderboard", methods=["GET"])
def get_leaderboard():
    """Lấy danh sách Top 10 người chơi có điểm số cao nhất"""
    query = """
    MATCH (p:Player)
    RETURN p.username AS username, p.score AS score, p.coins AS coins
    ORDER BY p.score DESC
    LIMIT 10
    """
    with driver.session() as session:
        results = session.run(query)
        leaders = [{
            "username": r["username"],
            "score": r["score"],
            "coins": r["coins"]
        } for r in results]
    return jsonify(leaders)


# ==================== API CỬA HÀNG ĐỔI QUÀ ====================
@app.route("/api/gifts", methods=["GET"])
def get_gifts():
    """Lấy danh mục quà tặng hiện có trong Neo4j"""
    query = """
    MATCH (g:Gift)
    RETURN g.id AS id, g.name AS name, g.coins_cost AS coins_cost, g.icon AS icon, g.desc AS desc
    ORDER BY g.coins_cost ASC
    """
    with driver.session() as session:
        results = session.run(query)
        gifts = [{
            "id": r["id"],
            "name": r["name"],
            "coins_cost": r["coins_cost"],
            "icon": r["icon"],
            "desc": r["desc"]
        } for r in results]
    return jsonify(gifts)


@app.route("/api/gifts/redeem", methods=["POST"])
def redeem_gift():
    """Đổi quà bằng xu tích lũy"""
    data = request.json or {}
    username = data.get("username")
    gift_id = data.get("gift_id")

    with driver.session() as session:
        check_query = """
        MATCH (p:Player {username: $username}), (g:Gift {id: $gift_id})
        RETURN p.coins AS current_coins, g.coins_cost AS cost, g.name AS gift_name
        """
        res = session.run(check_query, username=username, gift_id=gift_id).single()
        if not res:
            return jsonify({"error": "Không tìm thấy người chơi hoặc quà tặng"}), 400

        current_coins = res["current_coins"]
        cost = res["cost"]
        gift_name = res["gift_name"]

        if current_coins < cost:
            return jsonify({"error": f"Bạn không đủ xu! Cần {cost} xu nhưng bạn chỉ có {current_coins} xu."}), 400

        redeem_query = """
        MATCH (p:Player {username: $username}), (g:Gift {id: $gift_id})
        SET p.coins = p.coins - $cost
        CREATE (p)-[:DA_DOI {redeemed_at: timestamp()}]->(g)
        RETURN p.coins AS remaining_coins
        """
        final_res = session.run(redeem_query, username=username, gift_id=gift_id, cost=cost).single()
        return jsonify({
            "message": f"Chúc mừng! Bạn đã đổi thành công: {gift_name}",
            "remaining_coins": final_res["remaining_coins"]
        })


# ==================== API QUIZ GAME ====================
@app.route("/api/quiz", methods=["GET"])
def api_quiz():
    """Tự động sinh bộ câu hỏi trắc nghiệm từ đồ thị Neo4j"""
    questions = []
    
    with driver.session() as session:
        nodes_res = session.run("MATCH (n:TuGiac) RETURN n")
        nodes = [dict(record["n"]) for record in nodes_res]
        
        rels_res = session.run("""
            MATCH (a:TuGiac)-[r:TIEN_HOA_THANH]->(b:TuGiac)
            RETURN a.name AS parent, b.name AS child, r.dieu_kien AS cond
        """)
        rels = [record.data() for record in rels_res]

    if not nodes:
        return jsonify({"error": "Không có dữ liệu tứ giác"}), 400

    all_names = [n["name"] for n in nodes if "name" in n]

    # Loại 1: Dấu hiệu nhận biết
    for node in nodes:
        if node.get("nhan_biet"):
            correct = node["name"]
            wrong = random.sample([x for x in all_names if x != correct], min(3, len(all_names)-1))
            options = wrong + [correct]
            random.shuffle(options)
            questions.append({
                "question": f"Tứ giác nào có dấu hiệu nhận biết: \"{node['nhan_biet']}\"?",
                "options": options,
                "answer": correct,
                "explain": f"{correct} có tính chất/nhận biết: {node.get('tinh_chat', '')}"
            })

    # Loại 2: Công thức diện tích
    for node in nodes:
        if node.get("dien_tich") and node.get("dien_tich") != "Không có công thức chung đơn giản":
            correct = node["dien_tich"]
            other_formulas = list({n["dien_tich"] for n in nodes if n.get("dien_tich") and n["dien_tich"] != correct})
            wrong = random.sample(other_formulas, min(3, len(other_formulas)))
            options = wrong + [correct]
            random.shuffle(options)
            questions.append({
                "question": f"Công thức tính diện tích (S) của {node['name']} là gì?",
                "options": options,
                "answer": correct,
                "explain": f"Diện tích của {node['name']} được tính bằng công thức: {correct}"
            })

    # Loại 3: Quan hệ kế thừa / điều kiện suy diễn
    for r in rels:
        if r.get("cond"):
            correct = r["child"]
            wrong = random.sample([x for x in all_names if x != correct and x != r["parent"]], min(3, len(all_names)-2))
            options = wrong + [correct]
            random.shuffle(options)
            questions.append({
                "question": f"Khi {r['parent']} có thêm điều kiện \"{r['cond']}\" thì trở thành hình nào?",
                "options": options,
                "answer": correct,
                "explain": f"Theo phân cấp: {r['parent']} + ({r['cond']}) = {correct}"
            })

    random.shuffle(questions)
    return jsonify(questions[:5])


# ==================== API BÀI TOÁN HÌNH HỌC ====================
@app.route("/api/exercises", methods=["GET"])
def api_exercises():
    exercises = [
        {"id": 1, "shape_id": "hinh_thang", "shape_name": "Hình thang", "title": "Tính diện tích hình thang", "content": "Một mảnh đất hình thang có đáy lớn dài 18m, đáy bé dài 12m và chiều cao là 9m. Hãy tính diện tích mảnh đất này.", "formula_hint": "S = (a + b) × h / 2", "correct_answer": 135, "unit": "m²", "solution": "Áp dụng công thức S = (a + b) × h / 2:\nS = (18 + 12) × 9 / 2 = 30 × 9 / 2 = 135 m²."},
        {"id": 2, "shape_id": "hinh_thang", "shape_name": "Hình thang", "title": "Tìm chiều cao hình thang", "content": "Một hình thang có diện tích bằng 90 cm², biết tổng độ dài hai đáy là 30 cm. Tính chiều cao của hình thang đó.", "formula_hint": "h = 2 × S / (a + b)", "correct_answer": 6, "unit": "cm", "solution": "Từ công thức S = (a + b) × h / 2 => h = (2 × S) / (a + b).\nThay số: h = (2 × 90) / 30 = 180 / 30 = 6 cm."},
        {"id": 3, "shape_id": "hinh_binh_hanh", "shape_name": "Hình bình hành", "title": "Tính diện tích hình bình hành", "content": "Một khu vườn hình bình hành có độ dài đáy là 24m và chiều cao tương ứng là 15m. Tính diện tích khu vườn.", "formula_hint": "S = a × h", "correct_answer": 360, "unit": "m²", "solution": "Áp dụng công thức S = a × h:\nS = 24 × 15 = 360 m²."},
        {"id": 4, "shape_id": "hinh_binh_hanh", "shape_name": "Hình bình hành", "title": "Tính chu vi hình bình hành", "content": "Hình bình hành ABCD có độ dài cạnh a = 14 cm và cạnh b = 8 cm. Chu vi của hình bình hành đó là bao nhiêu?", "formula_hint": "P = 2 × (a + b)", "correct_answer": 44, "unit": "cm", "solution": "Áp dụng công thức P = 2 × (a + b):\nP = 2 × (14 + 8) = 2 × 22 = 44 cm."},
        {"id": 5, "shape_id": "hinh_chu_nhat", "shape_name": "Hình chữ nhật", "title": "Tính diện tích và chu vi hình chữ nhật", "content": "Một sân bóng đá mini hình chữ nhật có chiều dài 25m và chiều rộng 15m. Tính diện tích của sân.", "formula_hint": "S = a × b", "correct_answer": 375, "unit": "m²", "solution": "Áp dụng công thức S = a × b:\nS = 25 × 15 = 375 m²."},
        {"id": 6, "shape_id": "hinh_chu_nhat", "shape_name": "Hình chữ nhật", "title": "Tính độ dài đường chéo hình chữ nhật", "content": "Một màn hình tivi hình chữ nhật có chiều rộng 30 cm và chiều dài 40 cm. Tính độ dài đường chéo của màn hình.", "formula_hint": "d = √(a² + b²)", "correct_answer": 50, "unit": "cm", "solution": "Theo định lý Pythagore trong tam giác vuông:\nd = √(30² + 40²) = √(900 + 1600) = √2500 = 50 cm."},
        {"id": 7, "shape_id": "hinh_thoi", "shape_name": "Hình thoi", "title": "Tính diện tích hình thoi qua hai đường chéo", "content": "Một viên gạch trang trí hình thoi có độ dài hai đường chéo lần lượt là 16 cm và 12 cm. Diện tích của viên gạch là bao nhiêu?", "formula_hint": "S = (d₁ × d₂) / 2", "correct_answer": 96, "unit": "cm²", "solution": "Áp dụng công thức S = (d₁ × d₂) / 2:\nS = (16 × 12) / 2 = 192 / 2 = 96 cm²."},
        {"id": 8, "shape_id": "hinh_thoi", "shape_name": "Hình thoi", "title": "Tính chu vi hình thoi", "content": "Một biển báo hình thoi có cạnh dài 25 cm. Chu vi của biển báo đó là bao nhiêu?", "formula_hint": "P = 4 × a", "correct_answer": 100, "unit": "cm", "solution": "Vì 4 cạnh của hình thoi bằng nhau, P = 4 × a:\nP = 4 × 25 = 100 cm."},
        {"id": 9, "shape_id": "hinh_vuong", "shape_name": "Hình vuông", "title": "Tính diện tích hình vuông", "content": "Một mảnh vườn hình vuông có chu vi là 48m. Hỏi diện tích mảnh vườn đó là bao nhiêu mét vuông?", "formula_hint": "Cạnh a = P / 4, sau đó S = a²", "correct_answer": 144, "unit": "m²", "solution": "1. Độ dài cạnh hình vuông: a = 48 / 4 = 12 m.\n2. Diện tích mảnh vườn: S = a² = 12² = 144 m²."},
        {"id": 10, "shape_id": "hinh_dieu", "shape_name": "Hình diều", "title": "Tính diện tích khung diều", "content": "Một con diều giấy có hai thanh tre bắt chéo vuông góc với nhau đóng vai trò làm hai đường chéo dài 50 cm và 30 cm. Tính diện tích mặt diều.", "formula_hint": "S = (d₁ × d₂) / 2", "correct_answer": 750, "unit": "cm²", "solution": "Hai đường chéo vuông góc, S = (50 × 30) / 2 = 1500 / 2 = 750 cm²."},
        {"id": 11, "shape_id": "tu_giac", "shape_name": "Tứ giác", "title": "Tính góc còn lại của tứ giác", "content": "Tứ giác ABCD có số đo ba góc lần lượt là: góc A = 70°, góc B = 110°, góc C = 85°. Hỏi số đo góc D bằng bao nhiêu độ?", "formula_hint": "Tổng 4 góc trong tứ giác bằng 360°", "correct_answer": 95, "unit": "°", "solution": "Tổng các góc trong một tứ giác bằng 360°.\nSố đo góc D = 360° - (70° + 110° + 85°) = 360° - 265° = 95°."}
    ]
    return jsonify(exercises)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
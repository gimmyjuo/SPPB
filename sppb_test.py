from neo4j import GraphDatabase
import ollama
import sys

# --- 1. Neo4j 資料庫連線設定 ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "my-neo4j-SPPB" # !! 您的密碼 !!
DRIVER = None

def connect_db():
    """建立 (或重用) 全局的資料庫連線"""
    global DRIVER
    if DRIVER is None:
        try:
            DRIVER = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            DRIVER.verify_connectivity()
            print("[KG 連線成功...]")
        except Exception as e:
            print(f"\n[KG 連線錯誤] 無法連接到 Neo4j 資料庫: {e}", file=sys.stderr)
            print("請確認您的 Neo4j (SPPB) 實例是否正在 RUNNING 狀態。", file=sys.stderr)
            sys.exit(1)
    return DRIVER

# --- 2. Neo4j 檢索 (Retrieval) 函式 ---

def get_score_from_kg(test_name, time_taken):
    """
    (RAG 步驟 1)
    拿「秒數」去 KG 檢索「分數」。
    """
    driver = connect_db()
    cypher_query = """
    MATCH (r:Rule {for_test: $test_name})
    WHERE r.min_time <= $time AND r.max_time >= $time
    RETURN r.score AS score
    """
    score = 0
    with driver.session(database="neo4j") as session:
        result = session.run(cypher_query, test_name=test_name, time=time_taken)
        record = result.single()
        if record:
            score = record["score"]
    return score

def get_meaning_from_kg(test_name, score):
    """
    (RAG 步驟 2 - ✨ V5 新增功能 ✨)
    拿「分數」去 KG 檢索「專業評語 (事實)」。
    """
    driver = connect_db()
    cypher_query = """
    MATCH (r:Rule {for_test: $test_name, score: $score})
    RETURN r.meaning_ch AS meaning
    """
    meaning = "N/A" # Default
    with driver.session(database="neo4j") as session:
        result = session.run(cypher_query, test_name=test_name, score=score)
        record = result.single()
        if record and record["meaning"]:
            meaning = record["meaning"]
    return meaning

def get_sppb_interpretation(total_score):
    """
    (RAG 步驟 3)
    拿「總分」去 KG 檢索「總結判讀 (事實)」。
    """
    driver = connect_db()
    cypher_query = """
    MATCH (i:Interpretation)
    WHERE i.min_score <= $score AND i.max_score >= $score
    RETURN i.meaning_ch AS interpretation
    """
    result_meaning = "找不到對應的說明"
    with driver.session(database="neo4j") as session:
        result = session.run(cypher_query, score=total_score)
        record = result.single()
        if record:
            result_meaning = record["interpretation"]
    return result_meaning

def close_db_connection():
    """關閉資料庫連線"""
    global DRIVER
    if DRIVER is not None:
        DRIVER.close()
        DRIVER = None

# --- 3. LLM 生成 (Generation) 函式 ---

def generate_llm_report(kg_facts):
    """
    (RAG 步驟 4 - ✨ V5 核心更新 ✨)
    將「所有」從 KG 檢索到的「事實」餵給 LLM，
    並「強制」它只能進行語言組合，不准思考。
    """
    print("\n[LLM 正在生成報告...] (根據 KG 事實進行中...)")
    
    # --- 這是 Graph-RAG 最關鍵的「約束提示」(Constrained Prompt) ---
    prompt_message = f"""
    你是一個語言助理，你的「唯一」任務是將我提供的「事實清單」組合(拼接)成一段流暢的繁體中文報告。
    
    **規則：**
    1.  以「您好」開頭。
    2.  必須「依序」提及我提供的所有事實。
    3.  **絕對禁止** 提及「知識圖譜」或「KG」這幾個字。
    4.  **絕對禁止** 加入任何「事實清單」中**沒有**的資訊、評語或建議。
    5.  **絕對禁止** 產生幻覺。

    **【事實清單 (你唯一能用的資料)】：**
    * 事實 (平衡-並排)： "{kg_facts['balance_side_meaning']}"
    * 事實 (平衡-半並排)： "{kg_facts['balance_semi_meaning']}"
    * 事實 (平衡-直線)： "{kg_facts['balance_tandem_meaning']}"
    * 事實 (步行速度)： "{kg_facts['gait_meaning']}"
    * 事實 (椅子起站)： "{kg_facts['chair_meaning']}"
    * 事實 (總分)： "您的 SPPB 總分是：{kg_facts['total_score']} 分"
    * 事實 (總結)： "根據量表規則，這個分數代表：{kg_facts['interpretation']}"
    
    請開始生成報告：
    """
    
    try:
        response = ollama.chat(
            model='gemma3:1b', # <-- 使用您電腦上的 gemma3:1b
            messages=[
                {'role': 'user', 'content': prompt_message},
            ],
            stream=False 
        )
        return response['message']['content']
        
    except Exception as e:
        print(f"\n[LLM 錯誤] 無法連接到 Ollama 伺服器: {e}", file=sys.stderr)
        return None

# --- 4. 應用程式主要邏輯 (V5) ---
def main():
    print("--- SPPB 智慧判讀系統 V5 (Graph-RAG) ---")
    print("請輸入您在各項測試中測得的「原始秒數」。\n")
    
    kg_facts = {} # 我們用來儲存所有從 KG 檢索到的「事實」
    
    try:
        connect_db() # 立即建立連線
        
        # --- 1. 平衡測試 (檢索分數 + 檢索事實) ---
        print("--- 1. 平衡測試 (請輸入三項秒數) ---")
        
        time_side = float(input("  A. 並排站立 (秒): "))
        score_side = get_score_from_kg("並排站立", time_side)
        kg_facts['balance_side_meaning'] = get_meaning_from_kg("並排站立", score_side)
        
        time_semi = float(input("  B. 半並排站立 (秒): "))
        score_semi = get_score_from_kg("半並排站立", time_semi)
        kg_facts['balance_semi_meaning'] = get_meaning_from_kg("半並排站立", score_semi)
        
        time_tandem = float(input("  C. 直線站立 (秒): "))
        score_tandem = get_score_from_kg("直線站立", time_tandem)
        kg_facts['balance_tandem_meaning'] = get_meaning_from_kg("直線站立", score_tandem)

        balance_score_total = score_side + score_semi + score_tandem
        print(f"\n[Python 運算] 平衡總分(加總): {balance_score_total} 分") 
        
        # --- 2. 步行速度 (檢索分數 + 檢索事實) ---
        print("\n--- 2. 步行速度測試 ---")
        time_gait = float(input("  B. 走四公尺的時間 (秒): "))
        gait_score = get_score_from_kg("步行速度", time_gait)
        kg_facts['gait_meaning'] = get_meaning_from_kg("步行速度", gait_score)

        # --- 3. 椅子起站 (檢索分數 + 檢索事實) ---
        print("\n--- 3. 椅子起站測試 ---")
        time_chair = float(input("  C. 椅子起站五次 (秒): "))
        chair_score = get_score_from_kg("椅子起站", time_chair)
        kg_facts['chair_meaning'] = get_meaning_from_kg("椅子起站", chair_score)

        # --- 4. 總分 (計算 + 檢索事實) ---
        total_score = balance_score_total + gait_score + chair_score
        kg_facts['total_score'] = total_score
        print(f"\n[Python 運算] SPPB 總分是: {total_score} 分")

        interpretation = get_sppb_interpretation(total_score)
        kg_facts['interpretation'] = interpretation
        
        print(f"[KG 檢索完畢] 已收集所有事實，準備提交給 LLM...")

        # --- 5. 呼叫 LLM 生成 (RAG) ---
        llm_report = generate_llm_report(kg_facts)

        if llm_report:
            print("\n--- 您的 SPPB 總結報告 (KG-RAG 生成) ---")
            print("=" * 40)
            print(llm_report)
            print("=" * 40)
        else:
            print("[錯誤] LLM 無法生成報告。")

    except ValueError:
        print("\n[錯誤] 請務必輸入數字。", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[操作中斷] 使用者取消操作。")
    finally:
        close_db_connection()
        print("[KG 連線關閉。]")

# --- 執行程式 ---
if __name__ == "__main__":
    main()  
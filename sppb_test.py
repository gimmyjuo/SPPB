import pandas as pd
from neo4j import GraphDatabase
import sys
import time
import ollama

# --- 1. 設定區 ---
CSV_FILE = "SPPB_秒數輸入測試集.csv"          # 來源檔案
OUTPUT_FILE = "SPPB_完整測試報告_LLM.csv"    # 輸出檔案

#NEO4J_URI = "bolt://localhost:7687"
NEO4J_URI = "neo4j+s://58771106.databases.neo4j.io" 
NEO4J_USER = "neo4j"
#NEO4J_PASSWORD = "my-neo4j-SPPB"           # !! 請確認密碼 !!
NEO4J_PASSWORD = "opNmcTLoVU5w4i2zRzDy8ZWDlYmkur2FG76Ipdn_47Q"
LLM_MODEL = "gemma3:1b"                    # 您的 Ollama 模型名稱

# --- 2. 資料庫與工具函式 ---
DRIVER = None

def connect_db():
    global DRIVER
    if DRIVER is None:
        try:
            DRIVER = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            DRIVER.verify_connectivity()
        except Exception as e:
            print(f"\n[Error] 資料庫連線失敗: {e}")
            sys.exit(1)
    return DRIVER

def close_db():
    if DRIVER:
        DRIVER.close()

def get_score_from_kg(test_name, time_taken):
    driver = connect_db()
    cypher = """
    MATCH (r:Rule {for_test: $test_name})
    WHERE r.min_time <= $time AND r.max_time >= $time
    RETURN r.score AS score
    """
    try:
        with driver.session() as session:
            result = session.run(cypher, test_name=test_name, time=time_taken)
            record = result.single()
            return record["score"] if record else 0
    except:
        return 0

def get_meaning_from_kg(test_name, score):
    driver = connect_db()
    cypher = """
    MATCH (r:Rule {for_test: $test_name, score: $score})
    RETURN r.meaning_ch AS meaning
    """
    try:
        with driver.session() as session:
            result = session.run(cypher, test_name=test_name, score=score)
            record = result.single()
            return record["meaning"] if record else ""
    except:
        return ""

def get_interpretation(total_score):
    driver = connect_db()
    cypher = """
    MATCH (i:Interpretation)
    WHERE i.min_score <= $score AND i.max_score >= $score
    RETURN i.meaning_ch AS interp
    """
    try:
        with driver.session() as session:
            result = session.run(cypher, score=total_score)
            record = result.single()
            return record["interp"] if record else ""
    except:
        return ""

def generate_llm_report(facts):
    """呼叫 Ollama 生成報告"""
    prompt = f"""
    你是一個語言助理，你的任務是將以下「事實清單」組合成一段流暢的繁體中文報告。
    規則：
    1. 以「您好」開頭。
    2. 必須提及所有事實。
    3. 不得提及「知識圖譜」或「KG」。
    4. 不得捏造事實。
    
    事實清單：
    * 平衡測試(並排): {facts.get('bal_a_txt', '')}
    * 平衡測試(半並排): {facts.get('bal_b_txt', '')}
    * 平衡測試(直線): {facts.get('bal_c_txt', '')}
    * 步行速度: {facts.get('gait_txt', '')}
    * 椅子起站: {facts.get('chair_txt', '')}
    * 總分: {facts.get('total_score', 0)} 分
    * 總體評估: {facts.get('interp', '')}
    
    請生成報告：
    """
    try:
        res = ollama.chat(model=LLM_MODEL, messages=[{'role': 'user', 'content': prompt}], stream=False)
        return res['message']['content']
    except Exception as e:
        return f"[LLM Error] {e}"

def print_progress(iteration, total, prefix='', suffix='', length=40, fill='█'):
    """繪製進度條"""
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {iteration}/{total} ({percent}%) {suffix}')
    sys.stdout.flush()

# --- 3. 主程式 ---

def main():
    print("--- SPPB 批次測試啟動 (含 LLM 生成) ---")
    connect_db()
    
    try:
        # header=None 代表直接讀數據
        df = pd.read_csv(CSV_FILE, header=None)
    except Exception as e:
        print(f"讀取輸入檔失敗: {e}")
        return

    total_cases = len(df)
    results = []
    
    print(f"來源檔案: {CSV_FILE}")
    print(f"總筆數: {total_cases} (每筆皆會呼叫 LLM，請稍候...)\n")

    start_time = time.time()

    for i, row in df.iterrows():
        try:
            # 1. 解析輸入 (欄位 0-4)
            inputs = {
                'bal_a': float(row[0]), 'bal_b': float(row[1]), 'bal_c': float(row[2]),
                'gait': float(row[3]), 'chair': float(row[4])
            }
            
            # 2. KG 運算
            s_a = get_score_from_kg("並排站立", inputs['bal_a'])
            s_b = get_score_from_kg("半並排站立", inputs['bal_b'])
            s_c = get_score_from_kg("直線站立", inputs['bal_c'])
            s_g = get_score_from_kg("步行速度", inputs['gait'])
            s_ch = get_score_from_kg("椅子起站", inputs['chair'])
            
            act_total = s_a + s_b + s_c + s_g + s_ch
            interp = get_interpretation(act_total)
            
            # 3. 準備 LLM 素材
            facts = {
                'bal_a_txt': get_meaning_from_kg("並排站立", s_a),
                'bal_b_txt': get_meaning_from_kg("半並排站立", s_b),
                'bal_c_txt': get_meaning_from_kg("直線站立", s_c),
                'gait_txt': get_meaning_from_kg("步行速度", s_g),
                'chair_txt': get_meaning_from_kg("椅子起站", s_ch),
                'total_score': act_total,
                'interp': interp
            }
            
            # 4. 呼叫 LLM
            llm_output = generate_llm_report(facts)
            
            # 5. 比對答案 (欄位 10 為總分)
            expected_total = int(row[10])
            status = "PASS" if act_total == expected_total else "FAIL"

            # 6. 收集結果
            # 將原始 row 轉 list，再加上我們算出的新資料
            row_data = row.tolist()
            row_data.extend([act_total, status, llm_output])
            results.append(row_data)
            
            # 更新進度條
            print_progress(i + 1, total_cases, prefix='進度:', suffix=f'目前狀態: {status}', length=40)

        except Exception as e:
            # 錯誤處理
            err_row = row.tolist() + [0, "ERROR", str(e)]
            results.append(err_row)
            print_progress(i + 1, total_cases, prefix='進度:', suffix='Error!', length=40)

    # 結束換行
    sys.stdout.write('\n') 
    
    # --- 存檔 ---
    cols = [
        "In_BalA", "In_BalB", "In_BalC", "In_Gait", "In_Chair",
        "Ans_BalA", "Ans_BalB", "Ans_BalC", "Ans_Gait", "Ans_Chair", "Ans_Total",
        "實算總分", "驗證狀態", "LLM_生成報告"  # <--- 最後這一欄就是你要看的
    ]
    
    try:
        out_df = pd.DataFrame(results)
        # 嘗試設定欄位名稱 (若欄位數相符)
        if out_df.shape[1] == len(cols):
            out_df.columns = cols
        
        out_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        
        duration = time.time() - start_time
        print("-" * 60)
        print(f"完成！耗時 {duration:.1f} 秒")
        print(f"檔案已輸出至: {OUTPUT_FILE}")
        print("請直接打開 CSV 查看 'LLM_生成報告' 欄位。")
        
    except Exception as e:
        print(f"存檔失敗: {e}")

    close_db()

if __name__ == "__main__":
    main()
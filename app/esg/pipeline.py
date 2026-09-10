# Port of esg_score_calculator-master/utils/helper.py, line for line.
# Divergences (approved): ast.literal_eval instead of eval (failures still skipped); the LLM
# comes from llm_mod.get_llm() (model from settings); inputs are (filename, bytes) tuples.
# ast.literal_eval accepts and rejects exactly what eval did for this output (JSON's
# true/false/null still skip the chunk), so scores match production; json.loads would not.
# Added: prompts are checked before the fan-out so a missing esg_prompts doc fails the run
# loudly (spec) instead of silently scoring that category 0; a run where every category
# failed to score is neither stored nor cached (spec). check_old_composite_score is
# dropped (unused in the original).
import ast
import concurrent.futures
import hashlib
import logging
from collections import Counter
from datetime import datetime

from app.esg import llm as llm_mod
from app.esg.extract import process_files, split_text_into_chunks
from app.esg.store import insert_esg_collection, store_llm_response, get_llm_response, read_prompt

logger = logging.getLogger(__name__)


# Function to generate hash
def generate_hash(text, algorithm='sha256'):
    hash_object = hashlib.new(algorithm)
    hash_object.update(text.encode('utf-8'))
    return hash_object.hexdigest()


def analyze_text_with_gpt(text, category):
    # Define prompts for each ESG category
    try:
        prompt = read_prompt(category)
        prompt = prompt.format(
            text=text
        )
        response = llm_mod.get_llm().generate_score(prompt)
        return response, text
    except Exception as e:
        logger.error(f"Error with GPT-3.5 Turbo API: {e}")
        return None,None

def select_keyword(catorgory,keywords_list):
    prompt = f"""you are AI Assitant, you have great expert in ESG, you can select the best keyword from the list of keywords
    Before selecting the top 5 keyword from the given list of keywords, you need to check the following:
    1. Understand the category of the keywords
    2. Understand the context of the keywords
    3. Understand the relevance of the keywords
    5. Understand the importance of the keywords
    6. STRICTLY SELECT THE TOP 5 KEYWORDS which will be more relevant to the category of the keywords.
    {catorgory}

    {keywords_list}
    Provide the response in JSON format with the following fields:
    - "keywords": A list of keywords or phrase maximum 5.

    """
    try:
        response = llm_mod.get_llm().generate_score(prompt)
        logger.info(f"SAGAR {catorgory} {response}")
        return response
    except Exception as e:
        logger.error(f"Error with GPT-3.5 Turbo API: {e}")
        return None


# Function to aggregate scores from multiple chunks
def aggregate_scores(score_results,category):
    total_score = 0
    count = 0
    sectors = []
    industries = []
    postive_keywords = []
    for result in score_results:
        try:
            parsed_result = ast.literal_eval(result)
            score = parsed_result.get("score", 0)
            sector = parsed_result.get("sector", "")
            industry = parsed_result.get("industry", "")
            keywords = parsed_result.get("positive_keywords", [])
            total_score += score
            count += 1
            postive_keywords.extend(keywords)
            sectors.append(sector)
            industries.append(industry)
        except Exception as e:
            logger.error(f"Error parsing JSON: {e}")
            continue

    # Get most common sector and industry
    most_common_sector = Counter(sectors).most_common(1)[0][0] if sectors else ""
    most_common_industry = Counter(industries).most_common(1)[0][0] if industries else ""
    most_common_keywords = select_keyword(category,postive_keywords)
    most_common_keywords = ast.literal_eval(most_common_keywords)
    logger.info(most_common_keywords["keywords"])

    return round(total_score / count, 2) if count > 0 else 0, most_common_sector, most_common_industry, most_common_keywords["keywords"]


def analyze_chunk(chunk: str, category: str):
    logger.info(f"Analyzing chunk for {category}...")
    return analyze_text_with_gpt(chunk, category)

def evaluate_score(score):
    score = int(score)
    if score > 90:
        return "A+", "Outstanding"
    elif 80 <= score <= 90:
        return "A", "Excellent"
    elif 71 <= score <= 79:
        return "B+", "Very Good"
    elif 61 <= score <= 70:
        return "B", "Good"
    elif 40 <= score <= 60:
        return "C", "Average"
    else:
        return "D", "Below Average"

def calculate_esg_score_concurrent(files, company_id, report_year):
    try:
        processed_data = process_files(files)
        if not processed_data:
            return {"status":"error","message": "No text extracted from the input file or URL."}

        scores = {"Environment": [], "Social": [], "Governance": []}
        esg_records = []

        # Get filename safely
        filename = next((name for name, _ in files), "unknown_file")

        # Handle string-based data
        if isinstance(processed_data, str):
            logger.info("Processing extracted text as a single block...")
            hash_text = generate_hash(processed_data)
            logger.info(f"Company Hash: {hash_text}")

            llm_response = get_llm_response(hash_text)
            if llm_response:
                return llm_response

            for category in scores:
                read_prompt(category)  # fail loudly before any scoring if a prompt is missing

            text_chunks = split_text_into_chunks(processed_data, max_tokens=2000)

            with concurrent.futures.ThreadPoolExecutor() as executor:
                for category in scores:
                    logger.info(f"Analyzing {category}...")
                    futures = [executor.submit(analyze_chunk, chunk, category) for chunk in text_chunks]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            analysis, text = future.result()
                            if analysis:
                                esg_records.append({
                                    "analysis": analysis,
                                    "text": text,
                                    "filename": filename,
                                    "category": category
                                })
                                scores[category].append(analysis)
                        except Exception as e:
                            logger.error(f"Error analyzing chunk for {category}: {e}")

        # Handle list of page-wise objects
        elif isinstance(processed_data, list):
            logger.info("Processing extracted text as a list of pages...")
            combined_text = " ".join(page.get("text", "") for page in processed_data)
            hash_text = generate_hash(combined_text)
            logger.info(f"Company Hash: {hash_text}")

            llm_response = get_llm_response(hash_text)
            if llm_response:
                return llm_response

            for category in scores:
                read_prompt(category)  # fail loudly before any scoring if a prompt is missing

            with concurrent.futures.ThreadPoolExecutor() as executor:
                for category in scores:
                    logger.info(f"Analyzing {category}...")
                    futures = [
                        executor.submit(analyze_chunk, page.get("text", ""), category)
                        for page in processed_data if page.get("text", "").strip()
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            analysis, text = future.result()
                            if analysis:
                                page_no = next(
                                    (page.get("page_no") for page in processed_data if page.get("text", "").strip() == text.strip()),
                                    None
                                )
                                esg_records.append({
                                    "analysis": analysis,
                                    "text": text,
                                    "filename": filename,
                                    "page_no": page_no,
                                    "category": category
                                })
                                scores[category].append(analysis)
                        except Exception as e:
                            logger.error(f"Error analyzing page chunk for {category}: {e}")
        else:
            return {"error": "Unsupported data format returned from process_file_or_url."}

        # Aggregate results
        final_scores = {}
        for category in scores:
            try:
                avg_score, most_common_sector, most_common_industry, most_common_keywords = aggregate_scores(scores[category], category)
                final_scores[category] = {
                    "score": avg_score,
                    "sector": most_common_sector,
                    "industry": most_common_industry,
                    "positive_keywords": most_common_keywords
                }
            except Exception as e:
                logger.error(f"Error aggregating {category} scores: {e}")
                final_scores[category] = {
                    "score": 0,
                    "sector": "unknown",
                    "industry": "unknown",
                    "positive_keywords": []
                }

        # Final report
        final_report_data = {
            "environmental_score": final_scores['Environment']['score'],
            "social_score": final_scores['Social']['score'],
            "governance_score": final_scores['Governance']['score'],
            "composite_score": (
                0.30 * final_scores['Environment']['score'] +
                0.30 * final_scores['Social']['score'] +
                0.40 * final_scores['Governance']['score']
            ),
            "sector": final_scores['Environment']['sector'].capitalize(),
            "industry": final_scores['Environment']['industry'].capitalize(),
            "environmental_top_keywords": final_scores['Environment']['positive_keywords'],
            "social_top_keywords": final_scores['Social']['positive_keywords'],
            "governance_top_keywords": final_scores['Governance']['positive_keywords'],
            "report_date": datetime.now().strftime('%Y-%m-%d')
        }

        # Breakage (spec): if every category failed to score anything, the LLM never worked
        # (revoked key, no quota, ...). The aggregation except-path is what sets sector and
        # industry to "unknown", so an all-zero report with "Unknown" sector AND industry
        # means "nothing was scored", not "legitimately scored 0". Store nothing and cache
        # nothing -- the original cached these zeros in esg_hashes permanently.
        if (
            final_report_data["environmental_score"] == 0
            and final_report_data["social_score"] == 0
            and final_report_data["governance_score"] == 0
            and final_report_data["sector"] == "Unknown"
            and final_report_data["industry"] == "Unknown"
        ):
            logger.error("Every ESG scoring call failed; not storing or caching the report.")
            return {
                "status": "error",
                "message": "No valid ESG score could be computed — the AI scoring failed for "
                           "every page (check the OpenAI key/quota).",
            }

        # Evaluate score performance
        for key in ['environmental', 'social', 'governance', 'composite']:
            score = final_report_data[f"{key}_score"]
            performance, label = evaluate_score(score)
            final_report_data[f"{key}_score_performance"] = performance
            final_report_data[f"{key}_score_performance_label"] = label

        # Insert ESG data
        esg_records.append(final_report_data)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.submit(
                insert_esg_collection,
                company_id,
                esg_records,
                [name for name, _ in files],
                final_report_data['composite_score'],
                report_year
            )

        # Save LLM response hash
        store_llm_response(filename,company_id, hash_text, final_report_data)
        return final_report_data

    except Exception as e:
        logger.error(f"Unexpected error in ESG score calculation: {e}")
        return {"error": f"Unexpected error occurred: {str(e)}"}

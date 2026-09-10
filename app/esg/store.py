# Port of esg_score_calculator-master/utils/mongodb.py. Same collections, fields and
# semantics. Differences: the db comes from get_db() at call time; filenames are passed as
# strings (no UploadFile); insert_user returns the ObjectId (original: str of it); a missing
# prompt raises instead of returning None.
import logging

from bson import ObjectId

from app.core.db import get_db

logger = logging.getLogger(__name__)


def user_collection():
    return get_db()["user_details"]


def esg_collection():
    return get_db()["esg_report"]


def report_hash():
    return get_db()["esg_hashes"]


def prompts_collection():
    return get_db()["esg_prompts"]


def read_prompt(category):
    result = prompts_collection().find_one({"category": category})
    if not result:
        raise RuntimeError(f"ESG prompt for {category} is missing in esg_prompts")
    return result["prompt"]


def insert_user(fullname, email, company_name, mobile_number, file_names):
    try:
        new_file_names = list(file_names)
        existing_user = user_collection().find_one({"email": email})

        if not existing_user:
            # Insert new user with uploaded files
            user_data = {
                "fullname": fullname,
                "email": email,
                "company_name": company_name,
                "mobile_number": mobile_number,
                "file_names": new_file_names,
            }
            result = user_collection().insert_one(user_data)
            logger.info(f"User added successfully: {result.inserted_id}")
            return result.inserted_id
        else:
            # Only add new file names (avoid duplicates)
            existing_file_names = existing_user.get("file_names", [])
            unique_new_files = [f for f in new_file_names if f not in existing_file_names]

            if unique_new_files:
                user_collection().update_one(
                    {"_id": existing_user["_id"]},
                    {"$push": {"file_names": {"$each": unique_new_files}}}
                )
                logger.info(f"Appended new files for user: {existing_user['_id']}")

            return existing_user["_id"]
    except Exception as e:
        logger.error(f"Failed to insert or update user: {e}")
        return None


def insert_esg_collection(company_id, esg_records, filenames, composite_score, report_year):
    try:
        esg_report = {
            "company_id": ObjectId(company_id),
            "analysis": esg_records,
            "filename": list(filenames),
            "report_year": report_year,
            "composite_score": composite_score
        }
        result = esg_collection().insert_one(esg_report)
        logger.info(f"ESG Score added successfully: {result.inserted_id}")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"Failed to insert ESG score: {e}")
        return None


def store_llm_response(filename, company_id, hash_value, llm_response):
    try:
        result = report_hash().insert_one({
            "company_id": ObjectId(company_id),
            "filename": filename,
            "hash": hash_value,
            "llm_response": llm_response
        })
        logger.info(f"LLM response stored successfully: {result.inserted_id}")
    except Exception as e:
        logger.error(f"Failed to store LLM response: {e}")


def get_llm_response(hash_value):
    try:
        response = report_hash().find_one({"hash": hash_value})
        return response["llm_response"] if response else None
    except Exception as e:
        logger.error(f"Failed to fetch LLM response: {e}")
        return None


def get_llm_response_by_id(report_id):
    try:
        response = report_hash().find_one({"_id": ObjectId(report_id)})
        return response["llm_response"] if response else None
    except Exception as e:
        logger.error(f"Failed to fetch LLM response: {e}")
        return None


def get_esg_score(company_id):
    try:
        data = esg_collection().find(
            {"company_id": ObjectId(company_id)},
            {"report_year": 1, "composite_score": 1, "_id": 0}
        )

        data = list(data)
        logger.info(f"Data fetched: {data}")
        valid_data = [entry for entry in data if "report_year" in entry and "composite_score" in entry]

        if not valid_data:
            logger.info("No valid ESG score data available.")
            return {"status": False, "message": "No valid ESG score data available."}

        valid_data.sort(key=lambda x: int(x["report_year"].split('-')[0]))
        scores = {entry["report_year"]: entry["composite_score"] for entry in valid_data}

        for year, score in scores.items():
            logger.info(f"{year}: {score}")

        if len(valid_data) >= 2:
            latest = valid_data[-1]
            previous = valid_data[-2]
            difference = latest["composite_score"] - previous["composite_score"]
            flag = "positive" if difference > 0 else "negative"
            logger.info(f"Comparison: {latest['report_year']} ({latest['composite_score']}) vs {previous['report_year']} ({previous['composite_score']}) | Difference: {difference} | Flag: {flag}")

            return {
                "latest_year": latest["report_year"],
                "latest_score": latest["composite_score"],
                "previous_year": previous["report_year"],
                "previous_score": previous["composite_score"],
                "difference": difference,
                "trend_flag": flag,
                "history": valid_data
            }
        elif len(valid_data) == 1:
            only = valid_data[0]
            logger.info(f"Only one year data available: {only['report_year']} ({only['composite_score']})")
            return {
                "latest_year": only["report_year"],
                "latest_score": only["composite_score"],
                "previous_year": "N/A",
                "previous_score": "N/A",
                "difference": "N/A",
                "trend_flag": "positive",
                "history": valid_data
            }

    except Exception as e:
        logger.error(f"Failed to retrieve ESG scores: {e}")
        return {"status": False, "message": f"Error occurred: {e}"}

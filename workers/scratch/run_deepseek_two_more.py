import json

from app.core.config import DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, DEEPSEEK_MODEL_TRANSLATE
from app.modules.ai.llm_openai_compatible import DeepSeekProvider
from app.modules.ai.mistral_compose import translate_article_mistral

arts = json.load(open("/home/guillaume/two_more_articles.json"))
langs = ["fa", "ps", "ar", "ru", "zh", "hi", "es", "fr"]

out = {}
usage = {}
for key, a in arts.items():
    out[key] = {}
    usage[key] = {}
    for lang in langs:
        print(f"{key} {lang}...", flush=True)
        client = DeepSeekProvider(
            api_key=DEEPSEEK_API_KEY, api_base=DEEPSEEK_API_BASE, model=DEEPSEEK_MODEL_TRANSLATE
        )
        result = translate_article_mistral(
            english_title=a["title"] or "",
            english_summary=a["summary"] or "",
            english_body=a["body"] or "",
            target_language=lang,
            client=client,
        )
        out[key][lang] = result
        usage[key][lang] = client.usage_totals()
        with open("/home/guillaume/deepseek_two_more.json", "w") as f:
            json.dump({"translations": out, "usage": usage}, f, ensure_ascii=False, indent=2)

print("ALL DONE")

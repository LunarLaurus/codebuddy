import yaml
import openai

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config

_config = load_config()
openai.api_key = _config.get("openai_api_key")
MODEL = _config.get("openai_model", "gpt-4")

def generate_llm_summary(prompt, max_tokens=800, temperature=0.3):
    response = openai.ChatCompletion.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a code summarizer. Keep the response concise and relevant."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=max_tokens,
        temperature=temperature
    )
    return response.choices[0].message.content.strip()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # Wildberries
    wb_query: str = 'кросcовки'
    wb_max_products: int = 10_000
    wb_delay_min: float = 1.0
    wb_delay_max: float = 3.0

    # DeepSeek LLM
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-chat'
    llm_batch_size: int = 30

    # Перцентили для ценового fallback (когда LLM недоступна)
    # Эконом: нижние SEGMENT_P_ECONOMY долей (0.30 = 30%)
    # Стандарт: от SEGMENT_P_ECONOMY до SEGMENT_P_PREMIUM
    # Премиум: верхние (1 - SEGMENT_P_PREMIUM) долей (0.05 = 5%)
    segment_p_economy: float = 0.30
    segment_p_premium: float = 0.95

    # Redis cache
    redis_url: str = 'redis://localhost:6379/0'
    redis_cache_ttl: int = 30 * 24 * 3600  # 30 дней

    # AmoCRM
    amocrm_subdomain: str = ''
    amocrm_access_token: str = ''

    # App
    demo_mode: bool = True
    demo_limit: int = 50
    output_dir: str = 'output'


settings = Settings()

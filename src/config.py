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

    # AmoCRM
    amocrm_subdomain: str = ''
    amocrm_access_token: str = ''

    # App
    demo_mode: bool = True
    demo_limit: int = 50
    output_dir: str = 'output'


settings = Settings()

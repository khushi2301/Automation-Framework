from urllib.parse import urlparse
from typing import List
import yaml


class URLValidator:
    def __init__(self, allowed_domains: List[str]):
        self.allowed_domains = allowed_domains

    def validate_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False
            host = (parsed.hostname or '').lower()
            return any(host == d or host.endswith('.' + d) for d in self.allowed_domains)
        except Exception:
            return False

    def validate_base_url(self, url: str) -> bool:
        return self.validate_url(url)


class EnvironmentValidator:
    def __init__(self, config_path: str = "./config/applications.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.allowed_domains = self.config.get('allowed_domains', [])
        self.url_validator = URLValidator(self.allowed_domains)

    def validate_application(self, application: str) -> bool:
        return application in self.config.get('applications', {})

    def validate_environment(self, application: str, environment: str) -> bool:
        app_config = self.config.get('applications', {}).get(application, {})
        return environment in app_config.get('environments', {})

    def get_application_url(self, application: str, environment: str) -> str:
        app_config = self.config.get('applications', {}).get(application, {})
        env_config = app_config.get('environments', {}).get(environment, {})
        return env_config.get('url', '')

    def validate_url(self, url: str) -> bool:
        return self.url_validator.validate_url(url)

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: real-system tests that install software (require sudo and network access)",
    )

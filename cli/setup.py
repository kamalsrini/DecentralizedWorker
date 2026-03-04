from setuptools import setup, find_packages

setup(
    name="agentwork-cli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["PyYAML"],
    entry_points={
        "console_scripts": [
            "agentwork=agentwork.main:main",
        ],
    },
    python_requires=">=3.10",
    description="CLI for the AgentWork decentralized agent collaboration platform",
)

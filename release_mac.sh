gsed -i 's/tdsr2/tdsr-mac/g' pyproject.toml
poetry lock
poetry publish --build --extras macos
git checkout pyprojec.toml

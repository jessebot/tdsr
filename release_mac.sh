gsed -i 's/tdsr2/tdsr-mac/g' pyproject.toml
poetry lock
poetry publish --build
git checkout pyprojec.toml

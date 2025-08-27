# https://stackoverflow.com/questions/23144059/git-clone-ignoring-file

git clone --no-checkout --filter=blob:none https://github.com/openitools/openitools-ipcc.git

cd openitools-ipcc

git config core.sparseCheckoutCone false

git sparse-checkout disable

git sparse-checkout set '/*'

git sparse-checkout add '!/*/*/*.tar'

git read-tree -mu HEAD

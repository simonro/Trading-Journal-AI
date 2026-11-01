import os

# The app checks GitHub for a newer release on start; tests never reach the network.
os.environ["UPDATE_CHECK"] = "0"

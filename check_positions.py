
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

load_dotenv()

def main():
    if not mt5.initialize():
        print("initialize() failed, error code =", mt5.last_error())
        quit()

    login = int(os.getenv("MT5_LOGIN"))
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    if not mt5.login(login, password=password, server=server):
        print("login() failed, error code =", mt5.last_error())
        quit()

    positions = mt5.positions_get()
    if positions is None:
        print("No positions found, error code={}".format(mt5.last_error()))
    elif len(positions) > 0:
        print("Total positions:", len(positions))
        for position in positions:
            print(position)
    else:
        print("Positions: 0")

    mt5.shutdown()

if __name__ == "__main__":
    main()

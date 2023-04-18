

from chalice import Chalice, Cron

import requests
from ks_api_client import ks_api
import time
import requests
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from chalicelib import trade_config
# import pandas as pd

app = Chalice(app_name='trade-bot')


# *********************** function for email alerts *********************** #

def email_notification(send_add, rec_add, send_pass, mail_body, mail_subject):
    # Setup the MIME
    message = MIMEMultipart()
    message['From'] = send_add
    message['To'] = rec_add
    message['Subject'] = mail_subject  # The subject line
    # The body and the attachments for the mail
    message.attach(MIMEText(mail_body, 'plain'))
    # Create SMTP session for sending the mail
    session = smtplib.SMTP('smtp.gmail.com', 587)  # use gmail with port
    session.starttls()  # enable security
    session.login(send_add, send_pass)  # login with mail_id and password
    text = message.as_string()
    session.sendmail(send_add, rec_add, text)
    session.quit()


# ********************** send telegram notification *************** #

def send_telegram(user_, chat_message):
    chat_id = user_['telegram_chatid']
    telegram_bot_url = 'https://api.telegram.org/bot5558223629:AAGS38PFut9M__yxNYy-yUY7lozMSAFMfE0/sendMessage?chat_id=' + \
        chat_id+'&text="'+chat_message+'"'
    print('telegram bot url ->')
    print(telegram_bot_url)
    requests.get(telegram_bot_url)


# ************** function to fetch dynamic instrument_token definition ******************** #


def fetch_instrument_token(webhook_message, user_, kt_trade_config):
    headers = {'accept': 'application/json',
               "consumerkey": f"{user_['consumer_secret']}", "Authorization": f"Bearer {user_['access_token']}"}
    res = requests.get(
        kt_trade_config['instrument_token_script'], headers=headers).json()
    cash_instruments = res['Success']['cash']
    cash_df = pd.read_csv(cash_instruments, sep="|")
    tok_rec = cash_df[(cash_df.instrumentName == webhook_message['ticker']) & (cash_df.instrumentType == "EQ") & (
        cash_df.exchange == webhook_message['exchange'])]  # this must come from tradeview alert
    return tok_rec['instrumentToken']


# *********************** calculation to find no of instrument/trade against the margin ************** #

def calc_intrument_unit(kt_client, user_, webhook_message):
    # fetch dynamic available cash balance
    margin = kt_client.margin()
    if 'Success' not in margin:
        raise Exception('margin not available')

    av_cash_bal = margin['Success']['equity'][0]['cash']['availableCashBalance']
    print('availabel cash balance')
    print(av_cash_bal)

    # calculate risk per trade
    risk_percent = user_['risk_per_trade_percentage']
    risk_per_trade = av_cash_bal*risk_percent/100
    print('risk per trade')
    print(risk_per_trade)
    stop_loss = webhook_message['stop_loss']
    # place order now
    if 'buy' in webhook_message:
        trade_type = 'BUY'
        trade_price = webhook_message['buy']
        # calculate risk per share
        risk_per_share = float(trade_price) - float(stop_loss)
    elif 'sell' in webhook_message:
        trade_type = 'SELL'
        trade_price = webhook_message['sell']
        # calculate risk per share
        risk_per_share = float(stop_loss) - float(trade_price)
    else:
        raise Exception('order type could not be found in webhook_message')
    print('risk per share')
    print(risk_per_share)
    # calculate how many share you can buy with available amount
    no_of_share_you_can_trade = int(risk_per_trade/risk_per_share)
    print('no of share you can trade')
    print(no_of_share_you_can_trade)
    if no_of_share_you_can_trade == 0:
        raise Exception('cant trade share as no of share calculation is zero')
    # price require for the trade
    price_required_for_trade = trade_price*no_of_share_you_can_trade
    print('price rquird for trade')
    print(price_required_for_trade)
    return trade_type, no_of_share_you_can_trade, trade_price, stop_loss


# *********************** place_order() definition ***************************** #

def place_order(webhook_message, ks_api, user_, kt_trade_config):
    kt_client = ks_api.KSTradeApi(access_token=user_['access_token'], userid=user_['userid'], consumer_key=user_['consumer_key'], ip="127.0.0.1", app_id="DefaultApplication",
                                  host=kt_trade_config['host'], consumer_secret=user_['consumer_secret'])

    # Initiate login and generate OTT
    kt_login_details = kt_client.login(password=user_['kotak_password'])
    if "Success" not in kt_login_details:
        # TODO : notify failed user access to intended user on telegram chat
        send_telegram(user_, 'login un-successful for user->'+user_['userid'])
        # return {'message': 'login un-successful for user->'+user_['userid']}
        raise Exception('login un-successful for user->'+user_['userid'])
    # Complete login and generate session token
    kt_client_session = kt_client.session_2fa()
    if kt_client_session['clientCode'] != user_['clientCode']:
        # TODO : notify failed user access to intended user on telegram chat
        send_telegram(
            user_, 'client code does not matches for user->'+user_['userid'])
        # return {'message': 'client code does not matches for user->'+user_['userid']}
        raise Exception(
            'client code does not matches for user->'+user_['userid'])

    # fetch instrument token id from kotak equity/derivated scripts generated daily
    # kt_instument_token = fetch_instrument_token(webhook_message, user_ , kt_trade_config) # TODO : to enable to function for dynamic instument token fetch
    kt_instument_token = 1900
    trade_type, no_of_share_you_can_trade, trade_price, stop_loss = calc_intrument_unit(
        kt_client, user_, webhook_message)

    try:
        open_positions = kt_client.positions(position_type="TODAYS")
        exchange_order_report = kt_client.order_report()
        # check if any order is executed earlier or not, if yes then exit from the order as per alert direction
        if 'success' in exchange_order_report and len(exchange_order_report['success']) != 0 and open_positions['Success'][0]['netTrdQtyLot'] != 0:
            for placed_order in exchange_order_report['success']:
                placed_order_id = placed_order['orderId']
                placed_order_quantity = placed_order['orderQuantity']
                if (placed_order_id is not None) and (trade_type == 'BUY'):

                    # sell previous order if any
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                         quantity=int(placed_order_quantity), price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                    # now update the fund and re-calculate no of shares you can trade
                    trade_type, no_of_share_you_can_trade, trade_price, stop_loss = calc_intrument_unit(
                        kt_client, user_, webhook_message)
                    # now buy as per alert
                    '''order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                         quantity=int(no_of_share_you_can_trade), price=float(trade_price), trigger_price=float(stop_loss),
                                                         tag="string", validity="GFD", variety="REGULAR")
                    '''
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                         quantity=int(no_of_share_you_can_trade), price=0, trigger_price=float(stop_loss),
                                                         tag="string", validity="GFD", variety="REGULAR")

                elif (placed_order_id is not None) and (trade_type == 'SELL'):

                    # buy previous order if any
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                         quantity=int(placed_order_quantity), price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                    # now update the fund and re-calculate no of shares you can trade
                    trade_type, no_of_share_you_can_trade, trade_price, stop_loss = calc_intrument_unit(
                        kt_client, user_, webhook_message)
                    # now sell as per alert
                    '''order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                         quantity=int(no_of_share_you_can_trade), price=float(trade_price), trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                    '''
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                         quantity=int(no_of_share_you_can_trade), price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                else:
                    order_status = 'NA - existing'
                    print('no list of existing tran found')
                    kt_client.cancel_order(order_id=placed_order_id)
                    # return {'message': 'order type buy/sell not found in trading view alert'}
        else:

            if trade_type == 'BUY':
                '''
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                     quantity=int(no_of_share_you_can_trade), price=float(trade_price), trigger_price=float(stop_loss),
                                                     tag="string", validity="GFD", variety="REGULAR")
                '''
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                     quantity=int(no_of_share_you_can_trade), price=0, trigger_price=float(stop_loss),
                                                     tag="string", validity="GFD", variety="REGULAR")
            elif trade_type == 'SELL':
                '''
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                     quantity=int(no_of_share_you_can_trade), price=float(trade_price), trigger_price=float(stop_loss),
                                                     tag="string", validity="GFD", variety="REGULAR")
                '''
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                     quantity=int(no_of_share_you_can_trade), price=0, trigger_price=float(stop_loss),
                                                     tag="string", validity="GFD", variety="REGULAR")
            else:
                order_status = 'NA - first'
                print('inside first trade code block')

        return {'message': order_status['Success']['NSE']['message'] + ' | Order quantity is: '+str(order_status['Success']['NSE']['quantity'])}

    except Exception as ex:
        # send notification via BOT to respective user chat id TODO - later #
        # send_telegram(
        #    user_, 'order placement failed with given exception - '+str(ex))
        raise Exception(
            'order placement failed with given exception - '+str(ex))


# ******************************************************************************* #
# ********************** MAIN HANDLER / FUNCTION STARTS HERE ******************** #
# ******************************************************************************* #

@ app.route('/')
def index():
    return {'welcome to Trade bot home page !!'}


@ app.route('/trade_alert', methods=['POST'])
def trade_alert():
    try:
        request = app.current_request
        webhook_message = request.json_body
        print('webhook_message:')
        print(webhook_message)
        # check if pass phrase is correct in the alert message, if validated allow access
        if webhook_message['passphrase'] != trade_config.tradingview_alert_passphrase:
            # print('please check your passphrase, its not correct')
            raise Exception('please check your passphrase, its not correct.')

        # check if alert instruction is to place order OR for target match
        if 'target_hit' in webhook_message:
            if webhook_message['target_hit'] == 'buy':
                # check previously placed orders
                # trade off previously placed order with match target price
                return {'message': 'work in progress for target match critieria'}
            elif webhook_message['target_hit'] == 'sell':
                # check previously placed orders
                # trade off previously placed order with match target price
                return {'message': 'work in progress for target match critieria'}
            else:
                return {'message': 'target hit alert not properly set'}
        else:
            # check no of users in <trade_config> file. execute market order requests per user in parallel
            for user_ in trade_config.kotak_config['user_config']:
                # threading.Thread(target=place_order, args=(webhook_message,ks_api,user_ , trade_config.kotak_config,)).start()
                try:
                    place_order_result = place_order(webhook_message, ks_api, user_,
                                                     trade_config.kotak_config)
                    send_telegram(
                        user_, 'order placement successful. More details here - '+str(place_order_result))
                except Exception as e:
                    send_telegram(user_, 'error placing order for user :' +
                                  user_['user_name'] + '. See the exception here : '+str(e))
                    return {'message': 'error placing order. See the exception here : '+str(e)}
            # return {'message': 'Order placed successfully for all users'}
    except Exception as ex:
        return {'message': 'error placing order. See the exception here : '+str(ex)}


# ******************************************************************************** #
# **************** scheduler of event at 15:00 PM every day ********************** #
# ******************************************************************************** #

def per_user_round_off(user_, ks_api, kotak_config):
    # 1. check positions for TODAY - fetch open / executed trades from order placed for TODAY
    kt_client = ks_api.KSTradeApi(access_token=user_['access_token'], userid=user_['userid'], consumer_key=user_['consumer_key'], ip="127.0.0.1", app_id="DefaultApplication",
                                  host=kotak_config['host'], consumer_secret=user_['consumer_secret'])

    # Initiate login and generate OTT
    kt_login_details = kt_client.login(password=user_['kotak_password'])
    if "Success" not in kt_login_details:
        # TODO : notify failed user access to intended user on telegram chat
        raise Exception('login un-successful for user->'+user_['userid'])
    # Complete login and generate session token
    kt_client_session = kt_client.session_2fa()
    if kt_client_session['clientCode'] != user_['clientCode']:
        # TODO : notify failed user access to intended user on telegram chat
        raise Exception(
            'client code does not matches for user->'+user_['userid'])

    # fetch today's postion data
    open_positions = kt_client.positions(position_type="TODAYS")
    exchange_order_report = kt_client.order_report()
    # this means there are open trades which need to be squared off
    if open_positions['Success'][0]['netTrdQtyLot'] != 0:
        # Get's position by position_type.

        if 'success' in exchange_order_report:
            for placed_order in exchange_order_report['success']:
                try:
                    if placed_order['status'] == 'TRAD' and placed_order['product'] == 'MIS':
                        placed_instrument_token = placed_order['instrumentToken']
                        placed_order_quantity = placed_order['orderQuantity']
                        placed_order_id = placed_order['orderId']
                        place_order_type = placed_order['transactionType']

                        if place_order_type == 'BUY':
                            # sell the order with of same quantity
                            order_status = kt_client.place_order(order_type="MIS", instrument_token=placed_instrument_token, transaction_type="SELL",
                                                                 quantity=placed_order_quantity, price=0, trigger_price=0,
                                                                 tag="string", validity="GFD", variety="REGULAR")
                            send_telegram(
                                user_, 'order: '+str(placed_order_id)+'auto squared successfully')
                        elif place_order_type == 'SELL':
                            order_status = kt_client.place_order(order_type="MIS", instrument_token=placed_instrument_token, transaction_type="BUY",
                                                                 quantity=placed_order_quantity, price=0, trigger_price=0,
                                                                 tag="string", validity="GFD", variety="REGULAR")
                            send_telegram(
                                user_, 'order: '+str(placed_order_id)+'auto squared successfully')

                    else:
                        # order is still open therefore cancel the order
                        placed_order_id = placed_order['orderId']
                        kt_client.cancel_order(order_id=placed_order_id)
                        send_telegram(
                            user_, 'open order: '+str(placed_order_id)+'auto cancelled successfully')
                except Exception as e:
                    send_telegram(
                        user_, 'error: '+str(e)+' while auto squaring of order: '+str(placed_order_id))
    else:
        send_telegram(
            user_, 'There are no open trades for settlement, all were settled.')


@ app.schedule(Cron(30, 9, '?', '*', 'MON-FRI', '*'))
def daily_round_off_exit(event):
    try:
        for user_ in trade_config.kotak_config['user_config']:
            # threading.Thread(target=per_user_round_off, args=(user_,)).start()
            per_user_round_off(user_, ks_api, trade_config.kotak_config)

    except Exception as e:
        send_telegram(user_, 'error for user :' +
                      user_['user_name'] + '. See the exception here : '+str(e))

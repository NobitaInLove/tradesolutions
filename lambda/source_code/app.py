

from chalice import Chalice, Cron

from ks_api_client import ks_api
import time
import requests
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from chalicelib import trade_config
#import pandas as pd

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


# *********************** place_order() definition ***************************** #

def place_order(webhook_message, ks_api, user_, kt_trade_config):
    kt_client = ks_api.KSTradeApi(access_token=user_['access_token'], userid=user_['userid'], consumer_key=user_['consumer_key'], ip="127.0.0.1", app_id="DefaultApplication",
                                  host=kt_trade_config['host'], consumer_secret=user_['consumer_secret'])

    # Initiate login and generate OTT
    kt_login_details = kt_client.login(password=user_['kotak_password'])
    if "Success" not in kt_login_details:
        # TODO : notify failed user access to intended user on telegram chat
        return {'message': 'login un-successful for user->'+user_['userid']}
    # Complete login and generate session token
    kt_client_session = kt_client.session_2fa()
    if kt_client_session['clientCode'] != user_['clientCode']:
        # TODO : notify failed user access to intended user on telegram chat
        return {'message': 'client code does not matches for user->'+user_['userid']}

    # fetch instrument token id from kotak equity/derivated scripts generated daily
    # kt_instument_token = fetch_instrument_token(webhook_message, user_ , kt_trade_config) # TODO : to enable to function for dynamic instument token fetch
    kt_instument_token = 1900

    # fetch dynamic available cash balance
    margin = kt_client.margin()
    if 'Success' not in margin:
        return {'message': 'margin not available.'}

    av_cash_bal = margin['Success']['equity'][0]['cash']['availableCashBalance']
    print('availabel cash balance')
    print(av_cash_bal)

    # calculate risk per trade
    risk_percent = user_['risk_per_trade_percentage']
    risk_per_trade = av_cash_bal*risk_percent/100
    print('risk per trade')
    print(risk_per_trade)

    # fetch price, SL etc from <strategy.order.alert_message>
    if webhook_message['strategy']['alert_message'] is None:
        return {'message': 'alert message not found in trading view alert'}
    else:
        order_comment = webhook_message['strategy']['alert_message']
        str_ = order_comment.split('-')
        for temp in str_:
            if 'short@' in temp:
                price = temp.split('@')[1]
            elif 'sl@' in temp:
                stop_loss = temp.split('@')[1]
            elif 'target@' in temp:
                target_price = temp.split('@')[1]
            else:
                return {'message': 'one of the params Price, stop loss, target price were not found in strategy.order.comment. Please check'}
                break

    # calculate risk per share
    risk_per_share = float(price) - float(stop_loss)
    print('risk per share')
    print(risk_per_share)
    # calculate how many share you can buy with available amount
    no_of_share_you_can_buy = int(risk_per_trade/risk_per_share)
    print('no of share you can buy')
    print(no_of_share_you_can_buy)
    # price require for the trade
    price_required_for_trade = price*no_of_share_you_can_buy
    print('price rquird for trade')
    print(price_required_for_trade)

    # place order now
    order_type = webhook_message['strategy']['order_action']
    print('order type'+str(order_type))
    try:
        exchange_order_report = kt_client.order_report()
        # check if any order is executed earlier or not, if yes then exit from the order as per alert direction
        if 'success' in exchange_order_report:
            for placed_order in exchange_order_report['success']:
                placed_order_id = placed_order['exchOrderId']
                placed_order_quantity = placed_order['orderQuantity']
                if (placed_order_id is not None) and (order_type == 'buy'):
                    # sell previous order if any
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                         quantity=placed_order_quantity, price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                    # now buy as per alert
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                         quantity=no_of_share_you_can_buy, price=price, trigger_price=stop_loss,
                                                         tag="string", validity="GFD", variety="REGULAR")

                elif (placed_order_id is not None) and (order_type == 'sell'):
                    # buy previous order if any
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                         quantity=placed_order_quantity, price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")
                    order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                         quantity=1, price=0, trigger_price=0,
                                                         tag="string", validity="GFD", variety="REGULAR")

                else:
                    kt_client.cancel_order(order_id=placed_order_id)
                    # return {'message': 'order type buy/sell not found in trading view alert'}
        else:
            if order_type == 'buy':
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="BUY",
                                                     quantity=no_of_share_you_can_buy, price=price, trigger_price=stop_loss,
                                                     tag="string", validity="GFD", variety="REGULAR")
            elif order_type == 'sell':
                order_status = kt_client.place_order(order_type="MIS", instrument_token=1900, transaction_type="SELL",
                                                     quantity=1, price=0, trigger_price=0,
                                                     tag="string", validity="GFD", variety="REGULAR")

    except Exception as ex:
        # send notification via BOT to respective user chat id TODO - later #
        # send email alert to customer email id #
        print('exception in order placement')
        send_add = "itsabhaytiwari@gmail.com"
        rec_add = user_['email_id']
        send_pass = "axorhsnenecnldyu"
        mail_body = str(ex)
        mail_subject = '<Order alert>'
        email_notification(send_add, rec_add, send_pass,
                           mail_body, mail_subject)
        return {'message': 'order placement failed with given exception - '+str(ex)}
    print('order status')
    print(order_status)


# ******************************************************************************* #
# ********************** MAIN HANDLER / FUNCTION STARTS HERE ******************** #
# ******************************************************************************* #

@app.route('/')
def index():
    return {'welcome to Trade bot home page !!'}


@app.route('/trade_alert', methods=['POST'])
def trade_alert():
    try:
        request = app.current_request
        webhook_message = request.json_body
        # check if pass phrase is correct in the alert message, if validated allow access
        if webhook_message['passphrase'] != trade_config.tradingview_alert_passphrase:
            print('please check your passphrase, its not correct')
            return {'message': 'please check your passphrase, its not correct.'}
        # check no of users in <trade_config> file. execute market order requests per user in parallel
        for user_ in trade_config.kotak_config['user_config']:
            #threading.Thread(target=place_order, args=(webhook_message,ks_api,user_ , trade_config.kotak_config,)).start()
            place_order_result = place_order(webhook_message, ks_api, user_,
                                             trade_config.kotak_config)
        # return {'message': 'Order placed successfully for all users'}
    except Exception as ex:
        return {'message': 'error placing order. See the exception here : '+str(ex)}


# ******************************************************************************** #
# **************** scheduler of event at 15:00 PM every day ********************** #
# ******************************************************************************** #

def per_user_round_off(user_):
    # 1. check positions for TODAY - fetch open / executed trades from order placed for TODAY
    kt_client = ks_api.KSTradeApi(access_token=user_['access_token'], userid=user_['userid'], consumer_key=user_['consumer_key'], ip="127.0.0.1", app_id="DefaultApplication",
                                  host=trade_config.kotak_config['host'], consumer_secret=user_['consumer_secret'])

    # Initiate login and generate OTT
    kt_login_details = kt_client.login(password=user_['kotak_password'])
    if "Success" not in kt_login_details:
        # TODO : notify failed user access to intended user on telegram chat
        return {'message': 'login un-successful for user->'+user_['userid']}
    # Complete login and generate session token
    kt_client_session = kt_client.session_2fa()
    if kt_client_session['clientCode'] != user_['clientCode']:
        # TODO : notify failed user access to intended user on telegram chat
        return {'message': 'client code does not matches for user->'+user_['userid']}

    # Get's position by position_type.
    pos_details = kt_client.order_report()
    # 2. delete non-traded / open order
    # 3. exit/close traded orders / square off daily positions
    # 4. send email to each user about exit
    send_add = 'itsabhaytiwari@gmail.com'
    rec_add = user_['email_id']
    send_pass = 'axorhsnenecnldyu'
    mail_body = ''
    mail_subject = '<Daily EXIT status>'
    email_notification(send_add, rec_add, send_pass, mail_body, mail_subject)


@app.schedule(Cron(30, 9, '?', '*', 'MON-FRI', '*'))
def daily_round_off_exit(event):
    try:
        for user_ in trade_config.kotak_config['user_config']:
            threading.Thread(target=per_user_round_off, args=(user_,)).start()

    except Exception as e:
        print('exception in daily round off job -'+str(e))

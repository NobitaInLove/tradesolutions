function emailparsef() {
  var label = GmailApp.getUserLabelByName("tradealerts");
  var thread = label.getThreads();
  var curr_date = new Date();
  
  // check how many threads first
  // match last message in the thread with today's date else exit
  var curr_date_str = curr_date.getDate()+'-'+curr_date.getMonth()+'-'+curr_date.getFullYear();
  for (var i = 0; i < thread.length; i++){
    if(curr_date_str === (thread[i].getLastMessageDate().getDate()+'-'+thread[i].getLastMessageDate().getMonth()+'-'+thread[i].getLastMessageDate().getFullYear())){
      //Logger.log('date matched')
      var messages_in_thread = thread[i].getMessages();
      for (var j = 0; j<messages_in_thread.length; j++){
        // if message in the thread is starred as set by the filter on gmail filter rule, then read that message only
        if (messages_in_thread[j].isStarred() && messages_in_thread[j].isUnread()){
          var plainbody = messages_in_thread[j].getPlainBody();
          // TODO - check if string is present in plain body or not string [++target_price@val:stop_loss@val:base_price@val++]
          var json_str = {};
          var regex = /[++]/;
          const ret = plainbody.search(regex);
          Logger.log('return value');
          Logger.log(ret);
          if(ret!==-1.0){
            const words = plainbody.split('++');
            const i = words[1].split(':');
            for(var j=0;j<i.length;j++){
              const k = i[j].split('@');
              json_str[k[0]] = k[1];
            }
            json_str['passphrase'] = 'happytrading12378911';
            // TODO - logic here for substring identify and call external trade api POST method
            var final_json_str = JSON.stringify(json_str);
            webhook_call(final_json_str);
            // after message is read, mark it read and remove star
            messages_in_thread[j].markRead();
            messages_in_thread[j].unstar();
          } else {
            Logger.log('string patter ++ was not found in alert plain body');
            messages_in_thread[j].markRead();
            messages_in_thread[j].unstar();
          }
          
          
          

        } else{
          Logger.log('no unread and starred message condition');
        }
    }
      
    } else {
      Logger.log('no alert in todays date');
    }


  }
}


function webhook_call(final_json_str) {
    var headers = {
      //Authorization: 'Bearer ' + accessToken
      'contentType': 'application/json'
  };  
  
  const params = {
    'method': 'POST',
    //'contentType':'application/x-www-form-urlencoded',
    'contentType':'application/json',
    'muteHttpExceptions':false,
    'payload' : final_json_str
  }
  const url = 'https://oqof4gmfbd.execute-api.ap-south-1.amazonaws.com/api/trade_alert';
  const response = UrlFetchApp.fetch(url, params);
  //const response = UrlFetchApp.getRequest(url, params);
  Logger.log(response);
}

// DTS2026061793849 — 一键登录书签小工具UI优化：卡片式引导、F12步骤、Cookie拼接格式
javascript:void(function(){
try{
var c=document.cookie;
if(c&&c.includes('BDUSS')){
fetch('__SERVER_URL__/api/cookie/receive',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({cookie:c})
}).then(function(r){return r.json()}).then(function(d){
if(d.ok){alert('\u2705 Cookie\u5df2\u53d1\u9001\u5230\u8f6c\u5b58\u5de5\u5177\uff01\u8bf7\u56de\u5230\u5de5\u5177\u9875\u9762\u67e5\u770b');}
else{alert('\u53d1\u9001\u5931\u8d25: '+d.message);}
}).catch(function(e){
alert('\u53d1\u9001\u5931\u8d25\uff0c\u8bf7\u786e\u4fdd\u8f6c\u5b58\u5de5\u5177\u6b63\u5728\u8fd0\u884c');
});
return;
}
var s=document.createElement('style');
s.textContent=
'.bm-overlay{position:fixed;top:0;left:0;right:0;bottom:0;z-index:999999;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;font-family:system-ui,-apple-system,sans-serif}'+
'.bm-card{background:#fff;border-radius:16px;padding:28px;max-width:500px;width:92%;box-shadow:0 20px 60px rgba(0,0,0,0.3)}'+
'.bm-card h3{margin:0 0 16px;font-size:18px;color:#1e293b}'+
'.bm-card p{margin:0 0 12px;color:#64748b;font-size:14px;line-height:1.6}'+
'.bm-card ol{margin:0 0 16px;padding-left:20px;color:#334155;font-size:14px;line-height:2.2}'+
'.bm-card .tip{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:14px;margin-bottom:16px;font-size:13px;color:#0369a1;line-height:1.6}'+
'.bm-card .close-btn{width:100%;padding:12px;background:#3b82f6;color:#fff;border:none;border-radius:10px;font-size:15px;cursor:pointer;font-weight:500}'+
'.bm-card .close-btn:hover{background:#2563eb}';
document.head.appendChild(s);
var o=document.createElement('div');
o.className='bm-overlay';
o.onclick=function(e){if(e.target===o)o.remove();};
var card=document.createElement('div');
card.className='bm-card';
card.innerHTML=
'<h3>\u26a0\ufe0f Cookie \u53d7\u4fdd\u62a4\uff0c\u9700\u624b\u52a8\u83b7\u53d6</h3>'+
'<p>\u767e\u5ea6\u7f51\u76d8\u7684 BDUSS \u8bbe\u7f6e\u4e86 HttpOnly \u6807\u8bb0\uff0c\u7f51\u9875\u811a\u672c\u65e0\u6cd5\u8bfb\u53d6\u3002\u8bf7\u6309\u4ee5\u4e0b\u6b65\u9aa4\u64cd\u4f5c\uff1a</p>'+
'<ol>'+
'<li>\u6309 <b>F12</b> \u6253\u5f00\u5f00\u53d1\u8005\u5de5\u5177</li>'+
'<li>\u70b9\u51fb\u9876\u90e8 <b>Application</b>\uff08\u5e94\u7528\uff09\u6807\u7b7e</li>'+
'<li>\u5de6\u4fa7\u680f\u627e\u5230 <b>Cookies</b> \u2192 <b>pan.baidu.com</b></li>'+
'<li>\u627e\u5230 <b>BDUSS</b> \u884c\uff0c<b>\u53cc\u51fb</b> Value \u5217\u590d\u5236\u5b8c\u6574\u503c</li>'+
'<li>\u540c\u6837\u590d\u5236 <b>STOKEN</b> \u7684\u503c</li>'+
'</ol>'+
'<div class="tip">'+
'<b>\ud83d\udca1 \u62fc\u63a5\u683c\u5f0f\uff1a</b><br>BDUSS=<i>\u4f60\u590d\u5236\u7684\u503c</i>; STOKEN=<i>\u4f60\u590d\u5236\u7684\u503c</i><br><br>'+
'\u62fc\u597d\u540e\u56de\u5230\u8f6c\u5b58\u5de5\u5177\u9875\u9762\uff0c\u5c55\u5f00\u5e95\u90e8<b>\u300c\u624b\u52a8\u7c98\u8d34 Cookie\u300d</b>\u7c98\u8d34\u5373\u53ef\u3002'+
'</div>'+
'<button class="close-btn" id="bm-close">\u77e5\u9053\u4e86\uff0c\u5173\u95ed\u6b64\u7a97\u53e3</button>';
o.appendChild(card);
document.body.appendChild(o);
document.getElementById('bm-close').onclick=function(){o.remove();};
}catch(e){alert('\u6267\u884c\u51fa\u9519: '+e.message);}
}());

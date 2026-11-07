const api=async(path,opts={})=>{const r=await fetch('/api'+path,{...opts,headers:{'Content-Type':'application/json',...(opts.headers||{})}});if(!r.ok){let d={};try{d=await r.json()}catch{}throw Error(d.detail||`Erro ${r.status}`)}return r.status===204?null:r.json()};
async function login(e,redirect){e.preventDefault();const f=new FormData(e.target);try{await api('/auth/login',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})});location.href=redirect}catch(err){document.querySelector('#notice').textContent=err.message}}
async function logout(){await api('/auth/logout',{method:'POST'});location.reload()}
function fmt(seconds){seconds=Math.max(0,seconds);return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`}

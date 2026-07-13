(function(){
    function initBio(){
        const bio=document.getElementById('seiyuuBio');
        const toggle=document.getElementById('seiyuuBioToggle');
        if(!bio||!toggle)return;
        if(bio.scrollHeight<=bio.clientHeight+4){toggle.hidden=true;return;}
        toggle.addEventListener('click',function(){
            const expanded=bio.classList.toggle('is-expanded');
            toggle.textContent=expanded?'Show less':'Show more';
            toggle.setAttribute('aria-expanded',String(expanded));
        });
    }
    function initRoles(){
        const button=document.getElementById('seiyuuLoadMore');
        if(!button)return;
        button.addEventListener('click',function(){
            document.querySelectorAll('[data-extra-role]').forEach(function(card){card.hidden=false;});
            button.remove();
        });
    }
    document.addEventListener('DOMContentLoaded',function(){initBio();initRoles();});
})();

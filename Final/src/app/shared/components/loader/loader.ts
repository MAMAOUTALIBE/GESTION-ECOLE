import { Component, OnInit } from '@angular/core';

@Component({
    selector: 'app-loader',
    templateUrl: './loader.html',
    styleUrls: ['./loader.scss'],
    standalone:false,
})
export class Loader implements OnInit {
  classname: string='';
  constructor(){
    if(localStorage.getItem('Loader') == 'enable'){
      setTimeout(() => {
        document.querySelector('#loader')?.classList.add('d-none');
      }, 2000);
  }
}

  ngOnInit(): void {
    
  }
 

}


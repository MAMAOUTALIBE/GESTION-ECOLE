import { NgClass } from '@angular/common';
import { Component, input, ViewChild } from '@angular/core';

import { NgApexchartsModule, ApexOptions, ChartComponent } from 'ng-apexcharts';

@Component({
  selector: 'spk-apexcharts',
  imports: [NgApexchartsModule, NgClass],
  templateUrl: './spk-apexcharts.html',
  styleUrl: './spk-apexcharts.scss'
})
export class SpkApexcharts {
  @ViewChild("chart") chart!: ChartComponent;
  apxClass = input<string>()
  id = input<string>()
  chartOptions = input<ApexOptions>();
}



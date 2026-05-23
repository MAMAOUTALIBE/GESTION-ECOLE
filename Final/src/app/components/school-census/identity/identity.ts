import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { CensusPerson } from '../shared/school-census.models';

@Component({
  selector: 'app-identity',
  imports: [CommonModule, RouterModule],
  templateUrl: './identity.html',
  styleUrl: './identity.scss',
})
export class Identity {
  private route = inject(ActivatedRoute);
  private api = inject(CensusApiService);

  person: CensusPerson | null = null;
  personType = '';
  loading = true;
  error = '';

  ngOnInit() {
    const token = this.route.snapshot.paramMap.get('token') ?? '';
    this.api.identify(token).subscribe({
      next: (result) => {
        this.person = result.person;
        this.personType = result.personType;
        this.loading = false;
      },
      error: () => {
        this.error = 'Identité introuvable ou QR code non autorisé.';
        this.loading = false;
      },
    });
  }
}

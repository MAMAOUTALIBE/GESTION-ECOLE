import { Injectable } from '@angular/core';
import { AngularFireModule } from '@angular/fire/compat';
import { AngularFirestoreModule } from '@angular/fire/compat/firestore';
import { AngularFireDatabaseModule } from '@angular/fire/compat/database';
import { AngularFireAuthModule } from '@angular/fire/compat/auth';
import { environment } from '../../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class FirebaseService {
  constructor() {
    AngularFireModule.initializeApp(environment.firebase);
  }

  getFirestore() {
    return AngularFirestoreModule;
  }

  getDatabase() {
    return AngularFireDatabaseModule;
  }

  getAuth() {
    return AngularFireAuthModule;
  }
}

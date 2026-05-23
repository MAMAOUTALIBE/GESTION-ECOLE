
import { Component, effect, input, signal } from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { NgbNavModule } from '@ng-bootstrap/ng-bootstrap';
import { NgClass, UpperCasePipe } from '@angular/common';
interface Prism {
  [key: string]: string | undefined; // Allows  string key with string or undefined value
}
interface ShowCode {
  key?: string, value?: string
}

@Component({
  selector: 'app-showcode-card',
  templateUrl: './showcode-card.html',
  styleUrl: './showcode-card.scss',
  imports: [NgbNavModule,NgClass,UpperCasePipe]
})
export class ShowcodeCard {

  title = input<string>();
  subtitle = input<string>();
  classbody = input<string>();
  classCard = input<string>();
  classHeader = input<string>();
  prism = input<Prism>({});
  toggleStatus = false;
  showCodeArray: ShowCode[] = [{ key: '', value: "" }];
  toggleShowCode() {
    this.toggleStatus = !this.toggleStatus;
  }
  constructor(private clipboard: Clipboard) {
    effect(() => {
      // Used to convert the prism show code from Object to Array.
      this.showCodeArray = Object.entries(this.prism() ?? {}).map(([key, value]) => { return { key, value } });
    }
    )
  }
  copied = signal(false);
  activeTab = 0;
  copyCode(index: number) {
    const code = this.showCodeArray[index]?.value;
    if (code) {
      this.clipboard.copy(code);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 3000); // Reset after 3 seconds
    }
  }
}


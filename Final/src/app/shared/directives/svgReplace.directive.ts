import { Directive,  input, HostBinding } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

@Directive({
  selector: '[appSvgReplace]'
})
export class SvgReplaceDirective {

   // Input signal for SVG content
  appSvgReplace = input<string>('', { alias: 'appSvgReplace' });

  // Bind sanitized SVG content to innerHTML
  @HostBinding('innerHTML')
  get svgContent(): SafeHtml | string {
    const svgContent = this.appSvgReplace();
    return svgContent ? this.getSanitizedSVG(svgContent) : '';
  }

  constructor(private sanitizer: DomSanitizer) {}

  private getSanitizedSVG(svgContent: string): SafeHtml {
    // Sanitize SVG content to prevent XSS
    return this.sanitizer.bypassSecurityTrustHtml(svgContent);
  }
}




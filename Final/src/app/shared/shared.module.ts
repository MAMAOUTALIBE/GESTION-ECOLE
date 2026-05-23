import { NgModule } from '@angular/core';

import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { ColorPickerDirective} from 'ngx-color-picker';
import {  NgbModule } from '@ng-bootstrap/ng-bootstrap';
import { FullscreenDirective } from './directives/fullscreen.directive';
import { OverlayscrollbarsModule } from "overlayscrollbars-ngx";
import { HoverEffectSidebarDirective } from './directives/hover-effect-sidebar.directive';
import { Header } from './components/header/header';
import { Sidebar } from './components/sidebar/sidebar';
import { ContentLayout } from './layouts/content-layout/content-layout';
import { AuthenticationLayout } from './layouts/authentication-layout/authentication-layout';
import { Switcher } from './components/switcher/switcher';
import { PageHeader } from './components/page-header/page-header';
import { RightSidebar } from './components/right-sidebar/right-sidebar';
import { TabToTop } from './components/tab-to-top/tab-to-top';
import { Loader } from './components/loader/loader';
import { Footer } from './components/footer/footer';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { SvgReplaceDirective } from './directives/svgReplace.directive';

@NgModule({

    declarations:[
        Header,
        Sidebar,
        ContentLayout,
        AuthenticationLayout,
        Switcher,
        PageHeader,
        RightSidebar,
        TabToTop,
        Loader,
        Footer,

    ],
    imports: [
        CommonModule,
        RouterModule,
        NgbModule,
        FormsModule,
        ReactiveFormsModule,
        ColorPickerDirective,
        NgbModule,

        OverlayscrollbarsModule,
        SvgReplaceDirective,
        FullscreenDirective,
        HoverEffectSidebarDirective,
    ],
    exports: [
        Header,
        Sidebar,
        RightSidebar,
        ContentLayout,
        Switcher,
        PageHeader,
        TabToTop,
        Loader,
        Footer,

    ],
})

export class SharedModule { }

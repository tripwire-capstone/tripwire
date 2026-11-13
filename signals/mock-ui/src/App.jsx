import { Routes, Route } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import Home from './pages/Home';
import TripwireList from './pages/TripwireList';
import NewTripwire from './pages/NewTripwire';
import TripwireDetail from './pages/TripwireDetail';
import NotFound from './pages/NotFound';

// Every route exists from the start, so page work never needs to edit this file.
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/signals" element={<TripwireList />} />
        <Route path="/signals/new" element={<NewTripwire />} />
        <Route path="/signals/:id" element={<TripwireDetail />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
